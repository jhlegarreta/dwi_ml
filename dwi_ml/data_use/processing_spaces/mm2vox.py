
from __future__ import annotations
import numpy as np


def convert_mm2vox(length_mm: float, affine_vox2rasmm: np.ndarray)\
        -> float:
    """Convert a length from mm to voxel space (if the space is isometric).

    Parameters
    ----------
    length_mm : float
        Length in mm.
    affine_vox2rasmm : np.ndarray
        Affine to bring coordinates from voxel space to rasmm space, usually
        provided with an anatomy file.

    Returns
    -------
    length_vox : float
        Length expressed in isometric voxel units.

    Raises
    ------
    ValueError
        If the voxel space is not isometric (different absolute values on the
         affine diagonal).
    """
    diag = np.diagonal(affine_vox2rasmm)[:3]
    vox2mm = np.mean(np.abs(diag))

    # Affine diagonal should have the same absolute value for an isometric space
    if not np.allclose(np.abs(diag), vox2mm, rtol=5e-2, atol=5e-2):
        raise ValueError("Voxel space is not iso, cannot convert a scalar "
                         "length in mm to voxel space. "
                         "Affine provided : {}".format(affine_vox2rasmm))

    length_vox = length_mm / vox2mm
    return length_vox
