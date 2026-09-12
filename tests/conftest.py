"""Settings every test module shares.

HistGradientBoostingClassifier fits with OpenMP threads. On the small datasets
these tests train on, starting and synchronising threads costs more than it
saves, and it competes with anything else running on the machine: measured on
one training run, one thread took 9.5 seconds and eight took 14.0. The variable
has to be set before scikit-learn's compiled modules load, which is why it lives
here, the first file pytest imports. An explicit setting from outside is kept.
"""

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
