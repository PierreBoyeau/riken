import os
import numpy as np
import pandas as pd
from tqdm import tqdm
from tensorflow import flags
import tensorflow as tf
from keras.preprocessing.sequence import pad_sequences
from keras.models import load_model, Input, Sequential
from keras.layers import Embedding, Bidirectional, Dense, Dropout, CuDNNLSTM, Conv1D
from keras.layers import Activation, Permute, Reshape, Multiply, RepeatVector, Lambda, Concatenate
import keras.backend as K
from keras.models import Model
from keras.optimizers import Adam, SGD, RMSprop
from keras.callbacks import TensorBoard, ModelCheckpoint

from riken.protein_io import data_op, reader, replacement_mat, prot_features


chars = prot_features.chars
chars_to_idx = prot_features.chars_to_idx
n_chars = len(chars)

# STATIC_AA_TO_FEAT_M = prot_features.create_blosom_80_mat()
# STATIC_AA_TO_FEAT_M = prot_features.create_overall_static_aa_mat(normalize=True)
STATIC_AA_TO_FEAT_M = prot_features.create_overall_static_aa_mat(normalize=True)
# TODO: Ensure that new features (chemical properties of AA bring something to the model) via CV?

ONEHOT_M = np.zeros((n_chars + 1, n_chars + 1))
ONEHOT_M[1:, 1:] = np.eye(n_chars, n_chars)
MAXLEN = 500
LR = 1e-3


def get_all_features(seq, y, indices, pssm_format_fi='../data/psiblast/swiss/{}_pssm.txt'):
    sequences_filtered = []
    y_filtered = []
    pssm_filtered = []
    for (sen, y_value, id) in zip(tqdm(seq), y, indices):
        pssm_path = pssm_format_fi.format(id)
        try:
            pssm = pd.read_csv(pssm_path, sep=' ', skiprows=2, skipfooter=6, skipinitialspace=True)\
                .reset_index(level=[2, 3])
            pssm_feat = pssm.iloc[:MAXLEN].values
            seq_len, n_features_pssm = pssm_feat.shape
            # print(n_features_pssm)
            pssm_mat = np.zeros(shape=(MAXLEN, 42))
            pssm_mat[-seq_len:] = pssm_feat
            if np.isnan(pssm_mat).any():
                raise ValueError

        except Exception as e:
            print(e)
            print('Error!')
            pssm_mat = np.zeros(shape=(MAXLEN, 42))
        sequences_filtered.append(sen)
        y_filtered.append(y_value)
        pssm_filtered.append(pssm_mat)
    sequences_filtered = np.array(sequences_filtered)
    y_filtered = np.array(y_filtered)
    pssm_filtered = np.array(pssm_filtered)
    print(sequences_filtered.shape)
    print(y_filtered.shape)
    print(pssm_filtered.shape)
    print('{} examples'.format(len(sequences_filtered)))
    return sequences_filtered, pssm_filtered, y_filtered


def get_embeddings(inp):
    """
    Construct features from amino acid indexes
    :param inp:
    :return:
    """
    embed = Embedding(len(ONEHOT_M), output_dim=n_chars + 1, weights=[ONEHOT_M],
                      trainable=False, dtype='float32')(inp)

    static_embed = Embedding(STATIC_AA_TO_FEAT_M.shape[0], output_dim=STATIC_AA_TO_FEAT_M.shape[1],
                             weights=[STATIC_AA_TO_FEAT_M],
                             trainable=False, dtype='float32')(inp)
    h = Concatenate()([embed, static_embed])
    return h


def rnn_model_attention_psiblast(n_classes):
    aa_ind = Input(shape=(MAXLEN,), name='aa_indice')
    h = get_embeddings(aa_ind)

    psiblast_prop = Input(shape=(MAXLEN, 42), name='psiblast_prop', dtype=np.float32)

    h = Concatenate()([h, psiblast_prop])
    h = Conv1D(50, kernel_size=3, activation='relu', padding='same')(h)

    h = Dropout(rate=0.5)(h)
    h = Bidirectional(CuDNNLSTM(16, return_sequences=True))(h)

    attention = Dense(1)(h)
    attention = Lambda(lambda x: K.squeeze(x, axis=2))(attention)
    attention = Activation(activation='softmax')(attention)
    attention = RepeatVector(32)(attention)
    attention = Permute((2, 1))(attention)

    last = Multiply()([attention, h])
    last = Lambda(lambda x: K.sum(x, axis=1), output_shape=(32,))(last)

    h = Dense(n_classes, activation='softmax')(last)
    mdl = Model(inputs=[aa_ind, psiblast_prop], outputs=h)
    optimizer = Adam(lr=LR)
    mdl.compile(loss='categorical_crossentropy',
                optimizer=optimizer,
                metrics=['accuracy'])
    return mdl


def transfer_model(n_classes_new, mdl_path, prev_model_output_layer='lambda_2', freeze=False,
                   lr=1e-3, optim=Adam,
                   kernel_initializer='glorot_uniform', dropout_rate=0.0):
    prev_mdl = load_model(mdl_path)
    prev_mdl.layers.pop()
    if freeze:
        for layer in prev_mdl.layers:
            layer.trainable = False

    output_previous = prev_mdl.get_layer(prev_model_output_layer).output
    output_previous = Dropout(rate=0.3, name='last_dropout')(output_previous)
    new_output = Dense(n_classes_new, activation='softmax', kernel_initializer=kernel_initializer,
                       name='new_dense')(output_previous)

    mdl = Model(inputs=prev_mdl.input, outputs=new_output)

    optimizer = optim(lr=lr)
    mdl.compile(loss='categorical_crossentropy',
                optimizer=optimizer,
                metrics=['accuracy'])
    return mdl


if __name__ == '__main__':
    flags.DEFINE_integer('max_len', default=500, help='max sequence lenght')
    flags.DEFINE_float('lr', default=1e-3, help='learning rate')
    flags.DEFINE_float('memory_fraction', default=0.4, help='memory fraction')
    flags.DEFINE_string('data_path', default='/home/pierre/riken/data/swiss/swiss_with_clans.tsv',
                        help='path to tsv data')
    flags.DEFINE_string('pssm_format_file', default='', help='pssm_format_file')
    flags.DEFINE_string('key_to_predict', default='clan', help='key to predict (y)')
    flags.DEFINE_string('log_dir', default='./logs', help='path to save ckpt and summaries')
    flags.DEFINE_string('transfer_path', default=None, help='path to ckpt if doing transfer learning')
    flags.DEFINE_string('layer_name', default=None, help='Name of layer to use for transfer')
    flags.DEFINE_string('groups', default='NO', help='should we use groups')

    FLAGS = flags.FLAGS

    RANDOM_STATE = 42
    MAXLEN = FLAGS.max_len
    LR = FLAGS.lr
    DATA_PATH = FLAGS.data_path
    KEY_TO_PREDICT = FLAGS.key_to_predict
    LOG_DIR = FLAGS.log_dir
    TRANSFER_PATH = FLAGS.transfer_path
    LAYER_NAME = FLAGS.layer_name
    GROUPS = FLAGS.groups if FLAGS.groups!='NO' else None
    SPLITTER = data_op.shuffle_indices if GROUPS is None else data_op.group_shuffle_indices
    PSSM_FORMAT_FILE = FLAGS.pssm_format_file

    config = tf.ConfigProto()
    config.gpu_options.per_process_gpu_memory_fraction = 0.45
    K.set_session(tf.Session(config=config))

    df = pd.read_csv(DATA_PATH, sep='\t').dropna()
    df = df.loc[df.seq_len >= 50, :]

    try:
        df.loc[:, 'sequences'] = df.sequences_x
    except AttributeError:
        pass

    sequences, y = df['sequences'].values, df[KEY_TO_PREDICT]
    y = pd.get_dummies(y).values
    X = pad_sequences([[prot_features.safe_char_to_idx(char) for char in sequence]
                       for sequence in sequences], maxlen=MAXLEN)
    groups = None if GROUPS is None else df[GROUPS].values
    indices = df.index.values

    # Get train test indices before getting all features (potentially less
    # FOR CONSISTENCY PURPOSES
    train_inds, test_inds = SPLITTER(sequences, y, groups)

    X, pssm, y = get_all_features(X, y, indices, pssm_format_fi=PSSM_FORMAT_FILE)

    def convert_indices(old_to_new_dic, indices):
        res_ind = []
        for ind in indices:
            if ind in old_to_new_dic:
                res_ind.append(old_to_new_dic[ind])
        return np.array(res_ind)

    # train_inds = convert_indices(old_to_new_indices, train_inds)
    # test_inds = convert_indices(old_to_new_indices, test_inds)
    print(np.intersect1d(train_inds, test_inds))
    assert len(np.intersect1d(train_inds, test_inds)) == 0
    print(train_inds.shape, test_inds.shape)
    Xtrain, Xtest, ytrain, ytest = X[train_inds], X[test_inds], y[train_inds], y[test_inds]
    pssm_train, pssm_test = pssm[train_inds], pssm[test_inds]

    print(pssm_train[0])
    print(pssm_test[0])

    model = rnn_model_attention_psiblast(n_classes=y.shape[1]) if TRANSFER_PATH is None \
        else transfer_model(y.shape[1], TRANSFER_PATH, dropout_rate=0.3)

    print(model.summary())

    tb = TensorBoard(log_dir=LOG_DIR)
    ckpt = ModelCheckpoint(filepath=os.path.join(LOG_DIR, 'weights.{epoch:02d}-{val_loss:.2f}.hdf5'),
                           verbose=1, save_best_only=False, save_weights_only=False, mode='auto', period=1)
    model.fit([Xtrain, pssm_train], ytrain,  # bf [Xtrain, features_train], ...
              batch_size=64,
              epochs=12,
              validation_data=([Xtest, pssm_test], ytest),
              callbacks=[tb, ckpt])

    ypred = model.predict([Xtest, pssm_test])
    from sklearn.metrics import roc_auc_score
    print(ypred[0])
    print(roc_auc_score(ytest[:, 1], ypred[:, 1]))