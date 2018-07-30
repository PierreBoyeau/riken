import os
import tensorflow as tf

import rnn_model


"""
python trainer.py \
-train_path /home/pierre/train_data.tfrecords \
-val_path /home/pierre/val_data.tfrecords \
-log_dir ./logs_pssm_test \
-lr 1e-3 \
-n_classes 598 \
-lstm_size_list [128]
"""

tf.logging.set_verbosity(tf.logging.INFO)

flags = tf.flags
flags.DEFINE_string('train_path', '/home/pierre/riken/rnn/train_data.tfrecords', 'Path to training records')
flags.DEFINE_string('val_path', '/home/pierre/riken/rnn/val_data.tfrecords', 'Path to training records')
flags.DEFINE_string('log_dir', '/home/pierre/riken/rnn/results', 'Path to training records')
flags.DEFINE_integer('epochs', 10, 'Number of epochs to train the model on')
flags.DEFINE_float('lr', 1e-2, 'Maximum sequence lenght')
FLAGS = flags.FLAGS

train_params = {'lstm_size': 1288,
                'n_classes': 598,
                'max_size': 500,
                'dropout_keep_p': 0.3,
                'optimizer': tf.train.AdamOptimizer(learning_rate=FLAGS.lr),
                'conv_n_filters': 100}


def _parse_function(example_proto):
    features = {
        'sentence_len': tf.FixedLenFeature((), tf.int64, default_value=0),
        'tokens': tf.FixedLenFeature([train_params['max_size']], tf.int64),
        'pssm_li': tf.FixedLenFeature((train_params['max_size']*train_params['pssm_n_features']), tf.float32,
                                      default_value=0),
        'n_features_pssm': tf.FixedLenFeature((), tf.int64, default_value=0),
        'label': tf.FixedLenFeature((), tf.int64, default_value=0)
    }
    parsed_features = tf.parse_single_example(example_proto, features)
    labels = parsed_features.pop('label')
    return parsed_features, labels


def input_fn(path, epochs):
    dataset = tf.data.TFRecordDataset(path)
    dataset = dataset.map(_parse_function)
    dataset = dataset.shuffle(buffer_size=10000)
    dataset = dataset.repeat(count=epochs)
    batched_dataset = dataset.batch(batch_size=FLAGS.batch_size)
    iterator = batched_dataset.make_one_shot_iterator()
    nxt = iterator.get_next()
    return nxt


def train_input_fn():
    return input_fn(FLAGS.train_path, epochs=FLAGS.epochs)


def eval_input_fn():
    return input_fn(FLAGS.val_path, epochs=1)


def estimator_def(params):
    def model_fn(features, labels, mode=None, parameters=None, config=None):
        model = rnn_model.RnnModel(input=features['tokens'], pssm_input=features['pssm_input'],
                                   labels=labels, **parameters)
        if mode == tf.estimator.ModeKeys.PREDICT:
            predictions = model.probabilities
            return tf.estimator.EstimatorSpec(mode, predictions=predictions)

        if mode == tf.estimator.ModeKeys.EVAL:
            return tf.estimator.EstimatorSpec(
                mode, loss=model.loss, eval_metric_ops={'accuracy': model.acc})
        # Create training op.
        assert mode == tf.estimator.ModeKeys.TRAIN
        train_op = model.optimize
        return tf.estimator.EstimatorSpec(mode, loss=model.loss, train_op=train_op)

    return tf.estimator.Estimator(model_fn=model_fn,
                                  params=params)


if __name__ == '__main':
    mdl = estimator_def(train_params)
    evaluator = tf.contrib.estimator.InMemoryEvaluatorHook(estimator=mdl, input_fn=eval_input_fn)
    mdl.train(train_input_fn, hooks=[evaluator])

# def manual_train():
#     optimizer = tf.train.AdamOptimizer(learning_rate=FLAGS.lr)
#     next = train_input_fn()
#     next_eval = eval_input_fn()
#     with tf.variable_scope('model'):
#         model = rnn_model.RnnModel(lstm_size_list=FLAGS.lstm_size_list, n_classes=FLAGS.n_classes,
#                                    vocab_size=25, learning_rate=FLAGS.lr,
#                                    max_size=FLAGS.max_len, embed_dim=10, dropout_keep_p=FLAGS.dropout_keep_p,
#                                    optimizer=optimizer)
#     opt = model.optimize
#
#     loss_summ = tf.summary.scalar('loss', model.loss)
#     acc_summ = tf.summary.scalar('accuracy', model.acc)
#     tf.summary.histogram('predictions', tf.argmax(model.logits, 1))
#     merger = tf.summary.merge_all()
#     step = tf.train.get_or_create_global_step(graph=None)
#     increment_op = tf.assign(step, step+1)
#     logtensors = {
#         "step": tf.train.get_or_create_global_step(), "train_loss": model.loss, "train_acc": model.acc
#     }
#
#     hks = [
#         tf.train.SummarySaverHook(
#             save_steps=500,
#             summary_op=merger,
#             output_dir=os.path.join(FLAGS.log_dir, 'summaries')
#         ),
#         tf.train.CheckpointSaverHook(
#             FLAGS.log_dir,
#             save_secs=60 * 10
#         ),
#         tf.train.LoggingTensorHook(
#             logtensors,
#             every_n_iter=1,
#         )
#     ]
#     with tf.train.MonitoredTrainingSession(hooks=hks, checkpoint_dir=FLAGS.log_dir) as sess:
#         train_handle = sess.run(next.string_handle())
#         sentence_len, tokens, pssm_li, n_features_pssm, label = train_handle
#         eval_handle = sess.run(next_eval.string_handle())
#         sentence_len_eval, tokens_eval, pssm_li_eval, n_features_pssm_eval, label_eval = eval_handle
#
#         sess.run(next.initializer)
#         while not sess.should_stop():
#             _, step_val = sess.run([opt, increment_op], feed_dict={model.input: tokens,
#                                                          model.pssm_input: pssm_li,
#                                                          model.labels: label})


# if __name__ == '__main__':
#     manual_train()