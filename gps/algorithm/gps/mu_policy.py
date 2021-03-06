"""This file defines policy optimization for a MU policy."""
import numpy as np
import tensorflow as tf
from tensorflow.contrib import layers
from tensorflow.contrib.framework import arg_scope
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

from gps.algorithm.policy_opt.policy_opt import PolicyOpt


class MU_Policy(PolicyOpt):
    """Motor Unit policy.

    The global policy is trained on state->linear controller pairs.

    """

    def __init__(self, hyperparams, dX, dU):
        """Initializes the policy.

        Args:
            hyperparams: Dictionary of hyperparameters.
            dX: Dimension of state space.
            dU: Dimension of action space.

        Hyperparameters:
            random_seed: Random seed used for tensorflow.
            init_var: Initial policy variance
            epochs: Number of training epochs each iteration.
            batch_size: Batch size used during training. Must be a divisor of  M * N * (T - 1).
            weight_decay: L2 regularization of the network.
            N_hidden: Size of hidden layers.
            dZ: Dimension of the latent space.
            beta_kl: Weight of the KL-divergence term in loss function.
            N: Number of samples regulriztion.

        """
        PolicyOpt.__init__(self, hyperparams, dX, dU)
        self.dX = dX
        self.dU = dU

        tf.set_random_seed(self._hyperparams['random_seed'])
        self.var = self._hyperparams['init_var'] * np.ones(dU)
        self.epochs = self._hyperparams['epochs']
        self.batch_size = self._hyperparams['batch_size']
        self.weight_decay = self._hyperparams['weight_decay']
        self.N_hidden = self._hyperparams['N_hidden']
        self.dZ = self._hyperparams['dZ']
        self.beta_kl = self._hyperparams['beta_kl']
        self.N = self._hyperparams['N']
        self.dropout_rate = self._hyperparams['dropout_rate']

        self.graph = tf.Graph()  # Encapsulate model in own graph
        with self.graph.as_default():
            self._init_network()
            self._init_loss_function()
            self._init_solver()

            # Create session
            config = tf.ConfigProto()
            config.gpu_options.allow_growth = True  # Prevent GPS from hogging all memory
            self.sess = tf.Session(config=config)
            self.sess.run(tf.global_variables_initializer())

            self.saver = tf.train.Saver(max_to_keep=None)
            self.graph.finalize()

        self.policy = self  # Act method is contained in this class

    def _init_network(self):
        """Defines the tensorflow network."""
        # Placeholders for dataset
        self.state_data = tf.placeholder(tf.float32, (None, None, self.dX))
        self.K_data = tf.placeholder(tf.float32, (None, self.dU, self.dX))
        self.k_data = tf.placeholder(tf.float32, (None, self.dU))
        self.precision_data = tf.placeholder(tf.float32, (None, self.dU, self.dU))
        dataset = tf.data.Dataset.from_tensor_slices(
            (
                self.state_data,
                self.K_data,
                self.k_data,
                self.precision_data,
            )
        ).shuffle(10000).batch(self.batch_size).repeat()

        # Batch iterator
        self.iterator = dataset.make_initializable_iterator()
        state_batch, self.K_batch, self.k_batch, self.precision_batch = self.iterator.get_next()

        # Compose and normalize state batch
        state_batch = tf.concat(
            values=[
                state_batch,
            ], axis=1
        )

        # Other placeholders
        self.state_batch = tf.reshape(state_batch, (-1, self.dX))
        self.is_training = tf.placeholder(tf.bool, ())
        self.K_center = tf.placeholder(tf.float32, (self.dU, self.dX))
        self.K_scale = tf.placeholder(tf.float32, (self.dU, self.dX))

        with tf.variable_scope('state_normalization'):
            state_batch_normalized = tf.layers.batch_normalization(
                self.state_batch, training=self.is_training, center=False, scale=False, renorm=True
            )

        # Action estimator
        with tf.variable_scope('action_estimator'), arg_scope(
            [layers.fully_connected],
            activation_fn=tf.nn.leaky_relu,
            weights_regularizer=layers.l2_regularizer(scale=self.weight_decay)
        ):
            h = layers.fully_connected(state_batch_normalized, self.N_hidden)
            h = layers.fully_connected(h, self.N_hidden)
            h = layers.fully_connected(h, self.N_hidden)
            self.action_estimation = layers.fully_connected(h, self.dU, activation_fn=None)

        # Stabilizer estimator
        with tf.variable_scope('stabilizer_estimator'), arg_scope(
            [layers.fully_connected],
            activation_fn=tf.nn.leaky_relu,
            weights_regularizer=layers.l2_regularizer(scale=self.weight_decay),
        ):
            # Encoder
            h = layers.fully_connected(state_batch_normalized, self.N_hidden * self.dX)
            self.latent = layers.fully_connected(h, self.dZ, activation_fn=None)

            # Stabilizer Translation
            h = layers.fully_connected(self.latent, self.N_hidden * self.dX, biases_initializer=None)
            h = layers.dropout(h, keep_prob=1 - self.dropout_rate, is_training=self.is_training)
            h = layers.fully_connected(h, self.N_hidden * self.dX, biases_initializer=None)
            h = layers.dropout(h, keep_prob=1 - self.dropout_rate, is_training=self.is_training)
            self.stabilizer_estimation = tf.reshape(
                layers.fully_connected(h, self.dX * self.dU, activation_fn=None, biases_initializer=None),
                (-1, self.dU, self.dX)
            )

        self.action_regulation = tf.einsum(
            'inm,im->in',
            self.stabilizer_estimation * self.K_scale + self.K_center,  # Reverse K standardization
            self.state_batch,
        )
        self.action_out = self.action_estimation + self.action_regulation

    def _init_loss_function(self):
        """Defines the loss function."""
        # KL divergence action estimator loss
        lqr_action = tf.einsum(
            'ijk,ilk->ilj', self.K_batch, tf.reshape(self.state_batch, (self.batch_size, self.N, self.dX))
        ) + tf.expand_dims(self.k_batch, 1)

        delta_action = lqr_action - tf.reshape(self.action_out, (self.batch_size, self.N, self.dU))
        self.loss_kl_action_estimation = tf.reduce_mean(
            tf.einsum('ijk,ikl,ijl->ij', delta_action, self.precision_batch, delta_action)
        ) / 2

        # KL stabilizer loss
        self.loss_mse_stabilizer = tf.reduce_mean(
            tf.square(
                tf.reshape(self.stabilizer_estimation, (self.batch_size, self.N, self.dU, self.dX)) -
                tf.reshape((self.K_batch - self.K_center) / self.K_scale, (self.batch_size, 1, self.dU, self.dX))
            )
        )

        # KL divergence latent space
        latent_std = tf.sqrt(tf.linalg.diag_part(tf_cov(self.latent)))
        self.loss_latent = tf.reduce_mean(
            latent_std + tf.square(tf.reduce_mean(self.latent, axis=0)) - 1.0 - tf.log(latent_std)
        ) / 2

        # Regularization loss
        self.loss_reg_action = tf.losses.get_regularization_loss(scope='action_estimator')
        self.loss_reg_stabilizer = tf.losses.get_regularization_loss(scope='stabilizer_estimator')

        # Total loss
        self.loss_action = self.loss_kl_action_estimation + self.loss_reg_action
        self.loss_stabilizer = self.beta_kl * self.loss_latent + self.loss_mse_stabilizer + self.loss_reg_stabilizer

    def _init_solver(self):
        """Defines the optimizer.

        Uses on optimizer for each the action estimator and the stabilizer net.
        """
        optimizer_action = tf.train.AdamOptimizer()
        optimizer_stabilizer = tf.train.AdamOptimizer()
        with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
            solver_op_action = optimizer_action.minimize(
                loss=self.loss_action,
                var_list=tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='action_estimator'),
            )
            solver_op_stabilizer = optimizer_stabilizer.minimize(
                loss=self.loss_stabilizer,
                var_list=[
                    tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='stabilizer_estimator'),
                    tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='state_normalization')
                ]
            )

        self.solver_op = tf.group(solver_op_stabilizer, solver_op_action)
        self.optimizer_reset_op = tf.variables_initializer(
            optimizer_action.variables() + optimizer_stabilizer.variables()
        )

    def update(self, X, mu, prc, K, k, initial_policy=False, **kwargs):
        """Trains the MU model on the dataset."""
        M, N, T = X.shape[:3]
        N_ctr = M * (T - 1)

        X = X[:, :, :-1].transpose((0, 2, 1, 3)).reshape(N_ctr, N, self.dX)
        K = K[:, :-1].reshape(N_ctr, self.dU, self.dX)
        k = k[:, :-1].reshape(N_ctr, self.dU)
        prc = prc[:, :-1].reshape(N_ctr, self.dU, self.dU)

        # Standardize K
        self.K_scaler = StandardScaler().fit(K.reshape(N_ctr, self.dU * self.dX))

        # Normalize precision
        prc = prc * (10 * self.dU / np.mean(np.trace(prc, axis1=-2, axis2=-1)))

        # Reset optimizer
        self.sess.run(self.optimizer_reset_op)

        # Initialize dataset iterator
        self.sess.run(
            self.iterator.initializer,
            feed_dict={
                self.state_data: X,
                self.K_data: K,
                self.k_data: k,
                self.precision_data: prc,
            }
        )

        batches_per_epoch = int(N_ctr / self.batch_size)
        assert batches_per_epoch * self.batch_size == N_ctr, (
            '%d * %d != %d' % (batches_per_epoch, self.batch_size, N_ctr)
        )
        epochs = self.epochs if not initial_policy else 10
        losses = np.zeros((epochs, 3))
        pbar = tqdm(range(epochs))
        for epoch in pbar:
            for i in range(batches_per_epoch):
                losses[epoch] += self.sess.run(
                    [
                        self.solver_op,
                        self.loss_action,
                        self.loss_stabilizer,
                        self.loss_latent,
                    ],
                    feed_dict={
                        self.is_training: True,
                        self.K_scale: self.K_scaler.scale_.reshape(self.dU, self.dX),
                        self.K_center: self.K_scaler.mean_.reshape(self.dU, self.dX),
                    }
                )[1:]
            losses[epoch] /= batches_per_epoch
            pbar.set_description("Loss: %.6f/%.6f/%.6f" % (losses[epoch, 0], losses[epoch, 1], losses[epoch, 2]))

        # Visualize training loss
        from gps.visualization import visualize_loss
        visualize_loss(
            self._data_files_dir + 'plot_gps_training-%02d' % (self.iteration_count),
            losses,
            labels=['Action Estimator', 'Stabilizer', 'Latent']
        )
        self.sample_latent_space(X, N_test=50)

        # Optimize variance.
        A = np.mean(prc, axis=0) + 2 * N * T * self._hyperparams['ent_reg'] * np.ones((self.dU, self.dU))

        self.var = 1 / np.diag(A)
        self.policy.chol_pol_covar = np.diag(np.sqrt(self.var))

    def act(self, x, _, t, noise):
        """Decides an action for the given state/observation at the current timestep.

        Args:
            x: State vector.
            obs: Observation vector.
            t: Time step.
            noise: A dU-dimensional noise vector.

        Returns:
            A dU dimensional action vector.

        """
        u = self.sess.run(
            self.action_out,
            feed_dict={
                self.state_batch: [x],
                self.is_training: False,
                self.K_scale: self.K_scaler.scale_.reshape(self.dU, self.dX),
                self.K_center: self.K_scaler.mean_.reshape(self.dU, self.dX),
            }
        )[0]
        if noise is not None:
            if t is None:
                u += self.chol_pol_covar.T.dot(noise[0])
            else:
                u += self.chol_pol_covar.T.dot(noise[t])
        return u

    def prob(self, X):
        """Runs policy forward.

        Args:
            X: States (N, T, dX)

        """
        N, T = X.shape[:2]

        action = self.sess.run(
            self.action_out,
            feed_dict={
                self.state_batch: X.reshape(N * T, self.dX),
                self.is_training: False,
                self.K_scale: self.K_scaler.scale_.reshape(self.dU, self.dX),
                self.K_center: self.K_scaler.mean_.reshape(self.dU, self.dX),
            }
        ).reshape((N, T, self.dU))
        pol_sigma = np.tile(np.diag(self.var), [N, T, 1, 1])
        pol_prec = np.tile(np.diag(1.0 / self.var), [N, T, 1, 1])
        pol_det_sigma = np.tile(np.prod(self.var), [N, T])

        return action, pol_sigma, pol_prec, pol_det_sigma

    def sample_latent_space(self, x_train, N_test):
        """Takes samples from the lantent space and visualizes them."""
        from gps.visualization.latent_space import visualize_latent_space_tsne

        N, T = x_train.shape[:2]
        x_train = x_train.reshape(N * T, self.dX)

        z_train = self.sess.run(
            self.latent, feed_dict={
                self.state_batch: x_train,
                self.is_training: False,
            }
        )[:, None]

        # Compute latent states for random states
        x_test = np.random.multivariate_normal(np.mean(x_train, axis=0), np.cov(x_train, rowvar=0) * 2, size=N_test)
        z_test = self.sess.run(
            self.latent, feed_dict={
                self.state_batch: x_test,
                self.is_training: False,
            }
        )[:, None]

        np.savez_compressed(
            self._data_files_dir + 'latent_space-%02d' % (self.iteration_count),
            x_train=x_train,
            z_train=z_train,
            x_test=x_test,
            z_test=z_test
        )

        for perp in [10, 25, 50]:
            visualize_latent_space_tsne(
                self._data_files_dir + 'plot_latent_space-%02d_perp=%d' % (self.iteration_count, perp),
                x_train,
                z_train,
                x_test,
                z_test,
                perplexity_scale=perp,
                export_data=False,
            )

    def restore_model(self, data_files_dir, iteration_count):
        """Loads the network weighs from a file."""
        self.saver.restore(self.sess, data_files_dir + 'model-%02d' % (iteration_count))

    def store_model(self):
        """Saves the network weighs in a file."""
        self.saver.save(self.sess, self._data_files_dir + 'model-%02d' % (self.iteration_count))


def tf_cov(x):
    """Compute covariance in tensorflow."""
    mean_x = tf.reduce_mean(x, axis=0, keepdims=True)
    mx = tf.matmul(tf.transpose(mean_x), mean_x)
    vx = tf.matmul(tf.transpose(x), x) / tf.cast(tf.shape(x)[0], tf.float32)
    cov_xx = vx - mx
    return cov_xx
