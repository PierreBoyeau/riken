import numpy as np
from riken.tcn.tcn_keras import *
from keras.optimizers import SGD, RMSprop
from keras.callbacks import EarlyStopping
from sklearn.model_selection import ParameterSampler
from sklearn.metrics import roc_auc_score
import time

import tensorflow as tf

flags = tf.flags

if __name__ == '__main__':
    flags.DEFINE_string('result_path', default=None, help='Name of layer to use for transfer')
    FLAGS = flags.FLAGS

    RESULTS_PATH = FLAGS.result_path
    RANDOM_STATE = 42
    MAXLEN = 500
    DATA_PATH = '/home/pierre/riken/data/riken_data/complete_from_xlsx_v2COMPLETE.tsv'
    KEY_TO_PREDICT = 'is_allergenic'
    SPLITTER = data_op.group_shuffle_indices
    PSSM_FORMAT_FILE = '/home/pierre/riken/data/psiblast/riken_data_v2/{}_pssm.txt'
    INDEX_COL = 0

    df = pd.read_csv(DATA_PATH, sep='\t', index_col=INDEX_COL).dropna()
    df = df.loc[df.seq_len >= 50, :]
    sequences, y = df['sequences'].values, df[KEY_TO_PREDICT]
    y = pd.get_dummies(y).values
    X = pad_sequences([[prot_features.safe_char_to_idx(char) for char in sequence]
                       for sequence in sequences], maxlen=MAXLEN)
    indices = df.index.values
    groups = df['species'].values

    train_inds, test_inds = np.where(df.is_train)[0], np.where(df.is_train == False)[0]

    print('{} train examples and {} test examples'.format(len(train_inds), len(test_inds)))
    assert len(np.intersect1d(train_inds, test_inds)) == 0
    print(train_inds.shape, test_inds.shape)

    X, pssm, y = get_all_features(X, y, indices, pssm_format_fi=PSSM_FORMAT_FILE)

    print('NORMALIZATION ...')
    X = (X - X[train_inds].mean(axis=0)) / X[train_inds].std(axis=0)
    pssm = (pssm - pssm[train_inds].mean(axis=0)) / pssm[train_inds].std(axis=0)

    Xtrain, Xtest, ytrain, ytest = X[train_inds], X[test_inds], y[train_inds], y[test_inds]
    pssm_train, pssm_test = pssm[train_inds], pssm[test_inds]

    trainval_inds, val_inds = SPLITTER(Xtrain, ytrain, groups[train_inds], test_size=0.35)
    Xtrainval, Xval = Xtrain[trainval_inds], Xtrain[val_inds]
    pssm_trainval, pssm_val = pssm_train[trainval_inds], pssm_train[val_inds]
    ytrainval, yval = ytrain[trainval_inds], ytrain[val_inds]

    print('{} trainval examples and {} val examples'.format(len(trainval_inds), len(val_inds)))

    grid_params = {
        'depth': [6, 7, 8, 9],
        'n_filters': np.arange(5, 50),
        'kernel_size': [5, 7, 9],
        'dropout_rate': [0.05, 0.1, 0.2, 0.25, 0.3, 0.5],
        'optim': [Adam, RMSprop, SGD],
        'lr': [1e-1, 1e-2, 1e-3],
        'maxlen': [500],
        'batch_size': np.linspace(30, 150, num=50, dtype=int),
        'activation': ['tanh', 'relu', 'selu'],
        'kernel_initializer': ['glorot_uniform', 'glorot_normal'],
        'trainable_embeddings': [True, False],
    }

    grid = ParameterSampler(grid_params, n_iter=10000)
    res_df = pd.DataFrame()
    for param in grid:
        try:
            print(param)
            lr = param.pop('lr')
            optim_fn = param.pop('optim')
            param['optim'] = optim_fn(lr)
            batch_size = param.pop('batch_size')
            mdl = tcn_model(n_classes=2, **param)
            callback = EarlyStopping(patience=5)

            history = mdl.fit([Xtrainval, pssm_trainval], ytrainval, batch_size=batch_size,
                              callbacks=[callback],
                              epochs=25, validation_data=[[Xval, pssm_val], yval])

            param['lr'] = lr
            param['optim'] = optim_fn
            param['nb_epochs'] = callback.stopped_epoch
            param['test_score'] = roc_auc_score(yval[:, 1], mdl.predict([Xval, pssm_val])[:, 1])
            param['batch_size'] = batch_size
            param['history'] = history.history
            res_df = res_df.append(param, ignore_index=True)
            res_df.to_csv(RESULTS_PATH, sep='\t')
        except Exception as e:
            print('ERROR!!!!!')
            print(e)
            time.sleep(10)
            pass

