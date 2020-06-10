# -*- coding: utf-8 -*-
import numpy as np


# Checked!
class ValueHistoryMonitor(object):
    """ History of some value for each iteration during training, and mean
    value for each epoch.

    Example of usage: History of the loss during training.
        loss_monitor = ValueHistoryMonitor()
        ...
        # Call update at each iteration
        loss_monitor.update(2.3)
        ...
        loss_monitor.avg  # returns the average loss
        ...
        loss_monitor.end_epoch()  # call at epoch end
        ...
        loss_monitor.epochs  # returns the loss curve as a list
    """

    def __init__(self, name):
        self.name = name
        self.all_updates_history = []  # The list of values
        self.epochs_means_history = []  # The list of averaged values per epoch
        self.current_epoch_history = []  # The list of values in current epoch

    @property
    def num_updates(self):
        return len(self.all_updates_history)

    @property
    def num_epochs(self):
        return len(self.epochs_means_history)

    def update(self, value):
        """
        Note. Does not save the update if value is inf.

        Parameters
        ----------
        value: The value of the loss
        """
        if np.isinf(value):
            return

        self.all_updates_history.append(value)
        self.current_epoch_history.append(value)

    def end_epoch(self):
        """
        Reset the current epoch.
        Append the average of this epoch's losses.
        """
        self.epochs_means_history.append(np.mean(self.current_epoch_history))
        self.current_epoch_history = []

    def get_state(self):
        return {'name': self.name,
                'all_updates_history': self.all_updates_history,
                'current_epoch_history': self.current_epoch_history,
                'epochs_means_history': self.epochs_means_history}

    def set_state(self, state):
        self.name = state['name']
        self.all_updates_history = state['all_updates_history']
        self.current_epoch_history = state['current_epoch_history']
        self.epochs_means_history = state['epochs_means_history']


# Checked!
class EarlyStopping(object):
    """
    Object to stop training early if the loss doesn't improve after a given
    number of epochs ("patience").
    """

    def __init__(self, patience: int, min_eps: float = 1e-6):
        """
        Parameters
        -----------
        patience: int
            Maximal number of bad epochs we allow.
        min_eps: float, optional
            Precision term to define what we consider as "improving": when the
            loss is at least min_eps smaller than the previous best loss.
        """
        self.patience = patience
        self.min_eps = min_eps

        self.best = None
        self.n_bad_epochs = 0

    def should_stop(self, loss):
        """ Manage a loss step. Returns True if training should stop.

        Parameters
        ----------
        loss : float
            Loss value for a new training epoch

        Returns
        -------
        True if the number of epochs without improvements is more than the
        patience.
        """
        if self.best is None:
            self.best = loss
            return False

        if loss < self.best - self.min_eps:
            self.best = loss
            self.n_bad_epochs = 0
        else:
            self.n_bad_epochs += 1

        if self.n_bad_epochs >= self.patience:
            return True

        return False

    def get_state(self):
        """ Get object state """
        return {'patience': self.patience,
                'min_eps': self.min_eps,
                'best': self.best,
                'n_bad_epochs': self.n_bad_epochs}

    def set_state(self, state):
        """ Set object state """
        self.patience = state['patience']
        self.min_eps = state['min_eps']
        self.best = state['best']
        self.n_bad_epochs = state['n_bad_epochs']
