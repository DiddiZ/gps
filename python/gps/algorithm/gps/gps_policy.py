""" This file defines policy optimization for a tensorflow policy. """
import numpy as np
import tensorflow as tf
from tensorflow.contrib import layers
from tensorflow.contrib.framework import arg_scope
from tqdm import tqdm

from gps.algorithm.policy_opt.policy_opt import PolicyOpt


class GPS_Policy(PolicyOpt):
    """ Policy optimization using tensor flow for DAG computations/nonlinear function approximation. """

    def __init__(self, hyperparams, dX, dU):
        PolicyOpt.__init__(self, hyperparams, dX, dU)
        self.dX = dX
        self.dU = dU

        tf.set_random_seed(self._hyperparams['random_seed'])
        self.var = self._hyperparams['init_var'] * np.ones(dU)
        self.epochs = self._hyperparams['epochs']
        self.batch_size = self._hyperparams['batch_size']
        self.weight_decay = self._hyperparams['weight_decay']
        self.N_hidden = self._hyperparams['N_hidden']

        self.graph = tf.Graph()  # Encapsulate model in own graph
        with self.graph.as_default():
            self.init_network()
            self.init_loss_function()
            self.init_solver()

            # Create session
            config = tf.ConfigProto()
            config.gpu_options.allow_growth = True  # Prevent GPS from hogging all memory
            self.sess = tf.Session(config=config)
            self.sess.run(tf.global_variables_initializer())

            self.saver = tf.train.Saver(max_to_keep=None)
            self.graph.finalize()

        self.policy = self  # Act method is contained in this class

    def init_network(self):
        # Placeholders for dataset
        self.state_data = tf.placeholder(tf.float32, (None, self.dX))
        self.action_data = tf.placeholder(tf.float32, (None, self.dU))
        self.precision_data = tf.placeholder(tf.float32, (None, self.dU, self.dU))
        dataset = tf.data.Dataset.from_tensor_slices((
            self.state_data,
            self.action_data,
            self.precision_data,
        )).shuffle(10000).batch(self.batch_size).repeat()

        # Other placeholders
        self.is_training = tf.placeholder(tf.bool, ())

        # Batch iterator
        self.iterator = dataset.make_initializable_iterator()
        self.state_batch, self.action_batch, self.precision_batch = self.iterator.get_next()
        state_batch_normalized = tf.layers.batch_normalization(
            self.state_batch, training=self.is_training, center=False, scale=False, renorm=True
        )

        with arg_scope(
            [layers.fully_connected],
            activation_fn=tf.nn.relu,
            weights_regularizer=layers.l2_regularizer(scale=self.weight_decay)
        ):
            h = layers.fully_connected(state_batch_normalized, self.N_hidden)
            h = layers.fully_connected(h, self.N_hidden)
            h = layers.fully_connected(h, self.N_hidden)
            self.action_out = layers.fully_connected(h, self.dU, activation_fn=None)

    def init_loss_function(self):
        # KL divergence loss
        #  loss_kl = 1/2 delta_action^T * prc * delta_action
        delta_action = self.action_batch - self.action_out
        self.loss_kl = tf.reduce_mean(tf.einsum('in,inm,im->i', delta_action, self.precision_batch, delta_action)) / 2

        # Regularization loss
        self.loss_reg = tf.losses.get_regularization_loss()

        # Total loss
        self.loss = self.loss_kl + self.loss_reg

    def init_solver(self):
        optimizer = tf.train.AdamOptimizer()
        with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
            self.solver_op = optimizer.minimize(self.loss)
        self.optimizer_reset_op = tf.variables_initializer(optimizer.variables())

    def update(self, X, mu, prc, initial_policy=False, **kwargs):
        """
        Trains a GPS model on the dataset
        """
        M, N, T = X.shape[:3]
        N_train = M * N * (T - 1)

        # Reshape inputs.
        X = X[:, :, :-1].reshape((N_train, self.dX))
        mu = mu[:, :, :-1].reshape((N_train, self.dU))
        prc = np.reshape(np.repeat(prc[:, None, :-1], N, axis=1), (N_train, self.dU, self.dU))

        # Normalize precision
        prc = prc * (self.dU / np.mean(np.trace(prc, axis1=-2, axis2=-1)))

        # Reset optimizer
        self.sess.run(self.optimizer_reset_op)

        # Initialize dataset iterator
        self.sess.run(
            self.iterator.initializer, feed_dict={
                self.state_data: X,
                self.action_data: mu,
                self.precision_data: prc
            }
        )

        batches_per_epoch = int(N_train / self.batch_size)
        assert batches_per_epoch * self.batch_size == N_train, \
            '%d * %d != %d' % (batches_per_epoch, self.batch_size, N_train)
        epochs = self.epochs if not initial_policy else 10
        losses = np.zeros((epochs, 2))
        pbar = tqdm(range(epochs))
        for epoch in pbar:
            for i in range(batches_per_epoch):
                losses[epoch] += self.sess.run(
                    [
                        self.solver_op,
                        self.loss_kl,
                        self.loss_reg,
                    ], feed_dict={
                        self.is_training: True,
                    }
                )[1:]
            losses[epoch] /= batches_per_epoch
            pbar.set_description("GPS Loss: %.6f" % (np.sum(losses[epoch])))

        # Visualize training loss
        from gps.visualization import visualize_loss
        visualize_loss(
            self._data_files_dir + 'plot_gps_training-%02d' % (self.iteration_count),
            losses,
            labels=['KL divergence', 'L2 reg']
        )

        # Optimize variance.
        A = (np.sum(prc, 0) + 2 * N * T * self._hyperparams['ent_reg'] * np.ones((self.dU, self.dU))) / N_train

        self.var = 1 / np.diag(A)
        self.policy.chol_pol_covar = np.diag(np.sqrt(self.var))

    def act(self, x, _, t, noise):
        u = self.sess.run(
            self.action_out, feed_dict={
                self.state_batch: [x],
                self.is_training: False,
            }
        )[0]
        if noise is not None:
            if t is None:
                u += self.chol_pol_covar.T.dot(noise[0])
            else:
                u += self.chol_pol_covar.T.dot(noise[t])
        return u

    def prob(self, X):
        """
        Run policy forward.
        Args:
            X: States (N, T, dX)
        """
        N, T = X.shape[:2]

        action = self.sess.run(
            self.action_out,
            feed_dict={
                self.state_batch: X.reshape(N * T, self.dX),
                self.is_training: False,
            },
        ).reshape((N, T, self.dU))
        pol_sigma = np.tile(np.diag(self.var), [N, T, 1, 1])
        pol_prec = np.tile(np.diag(1.0 / self.var), [N, T, 1, 1])
        pol_det_sigma = np.tile(np.prod(self.var), [N, T])

        return action, pol_sigma, pol_prec, pol_det_sigma

    def restore_model(self, data_files_dir, iteration_count):
        self.saver.restore(self.sess, data_files_dir + 'model-%02d' % (iteration_count))

    def store_model(self):
        self.saver.save(self.sess, self._data_files_dir + 'model-%02d' % (self.iteration_count))
