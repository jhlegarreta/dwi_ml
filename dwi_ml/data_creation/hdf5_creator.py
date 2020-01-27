"""Dataset processing and configuration.

For the expected config file structure, see
dwi_ml.io.description_data_structure.py

Here, we define:
    class BundleConfig:
        Keeping all its parameters public as this class is only meant to be used
        here:
            .name
            .clustering_threshold_mm
            .removal_distance_mm
    class TractoDatasetCreatorGeneric:
        Public variables and functions:
            .subject_ids
            .bval
            .creator_from_json()
            .get_state_dict()
            .load_and_process_volume()
            .load_process_and_merge_bundles()
    class TractoDatasetLoaderDWI
    class TractoDatasetLoaderDWISH
    class TractoDatasetLoaderFODFSH
    class TractoDatasetLoaderFODFPeaks
We expect these classes to be used in a file such as create_dataset.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from typing import Dict, IO, List, Tuple, Union

import nibabel as nib
import numpy as np
from dipy.core.gradients import GradientTable
from dipy.io.stateful_tractogram import Space, StatefulTractogram
from dipy.io.streamline import load_tractogram
from dipy.tracking.utils import length


from scil_vital.shared.code.validation.subjects_validation import \
    list_equals
from dwi_ml.data_creation import validate_subject_list
from scil_vital.shared.code.signal.signal_utils import (
    compute_fodf, compute_sh_coefficients,
    compute_ssst_frf, resample_dwi)
from scil_vital.shared.code.transformation.sft import (
    compress_sft, remove_short_streamlines_from_sft,
    subsample_sft, resample_sft)
from scil_vital.shared.code.validation.tractogram_validation import (
    are_tractograms_in_same_space)


class BundleConfig(object):
    """Bundle configuration parameters."""

    def __init__(self, name: str, clustering_threshold_mm: float = None,
                 removal_distance_mm: float = None):
        """
        Parameters
        ----------
        name : str
            The name of the bundle.
        clustering_threshold_mm : float
            The clustering threshold applied before removing similar streamlines.
        removal_distance_mm : float
            The removal threshold used to remove similar streamlines.
        """
        self.name = name
        self.clustering_threshold_mm = clustering_threshold_mm
        self.removal_distance_mm = removal_distance_mm


class TractoDatasetCreatorGeneric(object):
    """Base class for a dataset processor."""

    def __init__(self, final_subjects: List[str] = None, bval: int = None,
                 minimum_length_mm: float = None, step_size_mm: float = None,
                 bundles: Dict = None):
        """
        Parameters
        ----------
        bval : int
            Filter the dMRI image to keep only this b-value (and b0s).
        minimum_length_mm : float
            Remove streamlines shorter than this length.
        step_size_mm : float
            Resample streamlines to this step size.
        subject_ids : list of str
            Subjects to include in this dataset.
        bundles : dict
            Bundle-wise parameters; these should include the name and
            subsampling parameters OR If empty, datasets will be treated as
            wholebrain tractograms.
        """
        self.bval = bval
        self._minimum_length_mm = minimum_length_mm
        self._step_size_mm = step_size_mm
        self.final_subjs = final_subjects

        if bundles:
            # Bundle-specific options
            self._bundles = []
            for name, config_dict in bundles.items():
                try:
                    bundle_config = BundleConfig(
                        name,
                        config_dict["clustering_threshold_mm"],
                        config_dict["removal_distance_mm"]
                    )
                    self._bundles.append(bundle_config)
                except KeyError as e:
                    raise ValueError("Bundle {} is missing configuration "
                                     "parameters: {}".format(name, e))
        else:
            # Datasets will be treated as wholebrain tractograms in
            # load_and_process_streamlines
            self._bundles = None

    @classmethod
    def creator_from_json(cls, json_file: Union[str, IO], raw_path: str,
                          *args, **kwargs) -> TractoDatasetCreatorGeneric:
        """ Create a DatasetConfigAbstract object from a json file.

        Parameters
        ----------
        raw_path: str
            Directory of folders.
        json_file : str or IO
            The input configuration file, wither as a string or an input stream.
        args: (...)
            #ToDo
        kwargs: (...)
            #ToDo

        Returns
        -------
        dataset_creator : TractoDatasetCreatorGeneric
            A valid dataset configuration.
        """

        # If json_file is a string, create the IO json_file
        if isinstance(json_file, str):
            json_file = open(json_file, 'r')

        # Load the json_file data
        raw_config = json.load(json_file)

        # Compare subject lists 1) defined by user 2) from json 3) whose files
        # are present in directory
        final_subjects = cls._verify_subject_lists(raw_path,
                                                   kwargs['subject_ids'],
                                                   raw_config['subject_ids'])

        # Create the creator
        dataset_creator = cls(final_subjects, *args, **kwargs, **raw_config)

        return dataset_creator

    @staticmethod
    def _verify_subject_lists(raw_path: str, chosen_subjs, json_subjs):

        # Find list of existing subjects from folders
        all_subjs = [str(f.name) for f in raw_path.iterdir()]
        if len(all_subjs) == 0:
            raise ValueError('No subject folders found!')

        if json_subjs is None and chosen_subjs is None:
            raise ValueError('You must provide subject list. Either when '
                             'calling the script or from the json file!')

        # Checking json_subjs
        if json_subjs is not None:
            non_existing, good_json_subjs, ignored = \
                validate_subject_list(all_subjs, json_subjs)
            if len(non_existing) > 0:
                raise ValueError('Following subjects are in your json file '
                                 'but their folders were not found: {}'
                                 .format(non_existing))
            if len(ignored) > 0:
                logging.info("Careful! NOT processing subjects {} "
                             "because they were not included in your json "
                             "file!".format(ignored))
            if chosen_subjs is None:
                return good_json_subjs

        # Checking chosen_subjs
        if chosen_subjs is not None:
            non_existing, good_chosen_subjs, ignored = \
                validate_subject_list(all_subjs, json_subjs)
            if len(non_existing) > 0:
                raise ValueError('Following subjects were chosen in option '
                                 '--subject_ids but their folders were not '
                                 'found: {}'.format(non_existing))
            if len(ignored) > 0:
                logging.info("Careful! NOT processing subjects {} "
                             "because they were not included in in option "
                             "--subject_ids!".format(ignored))
            if json_subjs is None:
                return good_chosen_subjs

        # Both json_subjs and chosen_subjs are not None.
        # Comparing both lists
        if not list_equals(good_chosen_subjs, good_json_subjs):
            raise ValueError('TRIED TO DEAL WITH OPTION --subject_ids AS'
                             'WAS ADDED BY (ANTOINE?). WHAT TO DO IN THE '
                             'CASE WHERE JSON INFO AND OPTION INFOS ARE NOT'
                             ' THE SAME?')
        return json_subjs

    def get_state_dict(self):
        """ Get a dictionary representation to store in the HDF file."""
        return {'bval':
                self.bval if self.bval else "",
                'minimum_length_mm':
                self._minimum_length_mm if self._minimum_length_mm else "",
                'step_size_mm':
                self._step_size_mm if self._step_size_mm else "",
                'subject_ids':
                self.final_subjs if self.final_subjs else "",
                'bundles':
                [b.name for b in self._bundles] if self._bundles else ""}

    def load_and_process_volume(self, dwi_image: nib.Nifti1Image,
                                gradient_table: GradientTable,
                                wm_mask_image: nib.Nifti1Image,
                                output_path: pathlib.Path) -> np.ndarray:
        """ Abstract method for processing a DWI volume for a specific
        dataset."""
        raise NotImplementedError

    def load_process_and_merge_bundles(self,
                                       bundles_path: pathlib.Path,
                                       dwi_ref: nib.Nifti1Image)\
            -> Tuple[StatefulTractogram, List[float]]:
        """Load and process a group of bundles and merge all streamlines
        together.

        Parameters
        ----------
        bundles_path : pathlib.Path
            Path to bundles folder.
        dwi_ref : np.ndarray
            Reference used to load and send the streamlines in voxel space.

        Returns
        -------
        output_tractogram : StatefulTractogram
            All streamlines in voxel space.
        output_lengths : list of floats
            The euclidean length of each streamline
        """
                                                                                                        # ToDo Antoine: with Timer("Processing streamlines", newline=True):
        # Initialize
        output_tractogram = None
        output_lengths = []
        n_original_streamlines = 0
        if not self._bundles:
            # If no bundles described in the json file, we will treat the files
            # found in bundles as wholebrain tractograms
            chosen_bundles_config = [BundleConfig(p.stem) for p in
                                     bundles_path.glob('*')]
            if len(chosen_bundles_config) == 0:
                raise ValueError("No bundles found in the boundles folder!")
        else:
            chosen_bundles_config = self._bundles
        available_bundles = list(bundles_path.iterdir())

        for bundle_config in chosen_bundles_config:
                                                                                                            # ToDo Antoine:  with Timer(
                                                                                                            #             "Processing tractogram: {}".format(tractogram_file),
                                                                                                            #             newline=True,
                                                                                                            #             color='green'
                                                                                                            #         ):
                                                                                                            #  Mais ici tractogram = bundle.

            bundle, bundle_original_count = self._load_and_process_one_bundle(
                bundle_config, available_bundles, bundles_path, dwi_ref)
            if bundle is None:
                continue

            # Keep track of original count
            n_original_streamlines += bundle_original_count

            # Compute euclidean lengths
            output_lengths.extend(length(bundle.streamlines))

            # Add processed bundle to output tractogram
            if output_tractogram is None:
                output_tractogram = bundle
            else:
                # Validate that tractograms are in the same space
                assert are_tractograms_in_same_space(output_tractogram,
                                                     bundle),\
                    "Inconsistent tractogram space: {}".format(bundle)
                output_tractogram.streamlines.extend(bundle.streamlines)

        # Transfer the streamlines to the reference space before bringing them
        # to VOX space. NOTE: This is done in case the streamlines were tracked
        # in a different space than the provided dataset reference
        if output_tractogram is None:
            output_streamlines_rasmm = []
        else:
            output_streamlines_rasmm = output_tractogram.streamlines
        output_tractogram = StatefulTractogram(output_streamlines_rasmm,
                                               dwi_ref,
                                               space=Space.RASMM)

        # Internal validation check
        output_tractogram.remove_invalid_streamlines()
        logging.debug("Ran internal tractogram validation; "
                      "Remaining: {}".format(len(output_tractogram)))

        # Final nb of streamlines
        logging.info("Final number of streamlines : "
                     "{} / {}".format(len(output_tractogram),
                                      n_original_streamlines))

        # Send to VOX space and make sure the origin is at the CENTER of the
        # voxel. NOTE: This is really important, otherwise interpolation will
        # be off by half a voxel.
        output_tractogram.to_vox()
        output_tractogram.to_center()

        return output_tractogram, output_lengths

    def _load_and_process_one_bundle(self, bundle_config: BundleConfig,
                                     available_bundles,                                                     # toDo available_bundles = List[str]?
                                     bundles_path: pathlib.Path,
                                     dwi_ref: nib.Nifti1Image):

        # Find the bundle
        regex = re.compile(".*_{}.t([rc])k".format(bundle_config.name))
        matches = [b for b in available_bundles if re.match(regex, str(b))]
        if len(matches) == 0:
            logging.warning("Bundle {} was not found in "
                            "path: {}".format(bundle_config.name,
                                              str(bundles_path)))
            return None
        if len(matches) > 1:
            raise ValueError("Bundle {} has matched "
                             "multiple files: {}"
                             .format(bundle_config.name, matches))
        bundle_file = matches[0]

        # Load the bundle
        logging.info("Processing bundle: {}".format(bundle_file))
        bundle = load_tractogram(str(bundle_file), reference=dwi_ref,
                                 to_space=Space.RASMM,
                                 trk_header_check=False,
                                 bbox_valid_check=False)
        if len(bundle) == 0:
            logging.warning("Bundle {} contains 0 streamlines, "
                            "skipping...".format(str(bundle_file)))
            return None

        # Keep count of the original number of streamlines
        bundle_original_count = len(bundle)
        logging.debug("Bundle contains {} streamlines"
                      .format(bundle_original_count))

        # Remove streamlines that are too short
        bundle = remove_short_streamlines_from_sft(bundle,
                                                   self._minimum_length_mm)
        logging.debug("Removed streamlines under "                                                  
                      "{}mm; Remaining: {}".format(self._minimum_length_mm,
                                                   len(bundle)))

        # Subsample bundles to keep only the closest to centroid (only if we
        # have bundle information, i.e. not wholebrain)
        if (bundle_config.clustering_threshold_mm is not None
                and bundle_config.removal_distance_mm is not None):
            bundle = subsample_sft(bundle,
                                      bundle_config.clustering_threshold_mm,
                                      bundle_config.removal_distance_mm)
            logging.debug("Subsampled bundle using clustering "
                          "threshold of  {}mm and a removal distance of "
                          "{}mm; Remaining: {}"
                          .format(bundle_config.clustering_threshold_mm,
                                  bundle_config.removal_distance_mm,
                                  len(bundle)))

        # Resample streamlines to have all the same step size
        if self._step_size_mm:
            bundle = resample_sft(bundle, self._step_size_mm)
            logging.debug("Resampled streamlines' step size to {}mm"
                          .format(self._step_size_mm))
        else:  # If no step size is defined, compress the streamlines
            bundle = compress_sft(bundle)

        return bundle, bundle_original_count


class TractoDatasetLoaderDWI(TractoDatasetCreatorGeneric):
    """Class containing all configuration options for creating a new DWI
    dataset."""

    def __init__(self, resample: bool, *args, **kwargs):
        """
        Parameters
        ----------
        resample : int
            Optional; resample the signal to this number of directions on the
            sphere.
        """
        super().__init__(*args, **kwargs)
        self.resample = resample

    def get_state_dict(self):
        """Get a dictionary representation to store in the HDF file."""
        state_dict = super().get_state_dict()
        state_dict['resample'] = self.resample

        return state_dict

    def load_and_process_volume(self, dwi_image: nib.Nifti1Image,
                                gradient_table: GradientTable,
                                wm_mask_image: nib.Nifti1Image,
                                output_path: pathlib.Path) -> np.ndarray:
        """Process a volume for raw DWI dataset, optionally resampling the
        gradient directions.

        Parameters
        ----------
        dwi_image : nib.Nifti1Image
            Diffusion-weighted images (4D)
        gradient_table : dipy.io.gradients.GradientTable
            Dipy object that contains all bvals and bvecs.
        wm_mask_image : nib.Nifti1Image
            Binary white matter mask.
        output_path : str
            Path to the output folder.

        Returns
        -------
        output : np.ndarray
            The processed output volume.
        """
        if self.resample:
            # Load and resample:
            # Brings to SH and then back to directions.
            output = resample_dwi(dwi_image, gradient_table, sh_order=6)
        else:
            # Load:
            output = dwi_image.get_fdata(dtype=np.float32)

        return output


class TractoDatasetLoaderDWISH(TractoDatasetCreatorGeneric):
    """Class containing all configuration options for creating a new DWI-SH
    dataset."""

    def __init__(self, sh_order: int = None, *args, **kwargs):
        """
        Parameters
        ----------
        sh_order : int
            The SH order used to fit the signal
        """
        super().__init__(*args, **kwargs)
        self.sh_order = sh_order
        if self.sh_order is None:
            raise ValueError("SH order must be provided")
        if self.sh_order not in [2, 4, 6, 8]:
            raise ValueError("SH order must be one of [2,4,6,8]")

    def get_state_dict(self):
        """Get a dictionary representation to store in the HDF file."""
        state_dict = super().get_state_dict()
        state_dict['sh_order'] = self.sh_order

        return state_dict

    def load_and_process_volume(self, dwi_image: nib.Nifti1Image,
                                gradient_table: GradientTable,
                                wm_mask_image: nib.Nifti1Image,
                                output_path: pathlib.Path) -> np.ndarray:
        """Process a volume for a DWI-SH dataset. Fits spherical harmonics to
        the diffusion signal.

        Parameters
        ----------
        dwi_image : nib.Nifti1Image
            Diffusion-weighted images (4D)
        gradient_table : dipy.io.gradients.GradientTable
            Dipy object that contains all bvals and bvecs.
        wm_mask_image : nib.Nifti1Image
            Binary white matter mask.
        output_path : str
            Path to the output folder.

        Returns
        -------
        output : np.ndarray
            The processed output volume.
        """
        output = compute_sh_coefficients(dwi_image, gradient_table,                                 # toDo Antoine: get_pams. J'ai pas checké ce que ça fait.
                                         sh_order=self.sh_order)
        return output


class TractoDatasetLoaderFODFSH(TractoDatasetCreatorGeneric):
    """Class containing all configuration options for creating a new fODF-SH
    dataset."""

    def __init__(self, sh_order: int = None, *args, **kwargs):
        """
        Parameters
        ----------
        sh_order : int
            The SH order used to fit the signal
        """
        super().__init__(*args, **kwargs)
        self.sh_order = sh_order
        if self.sh_order is None:
            raise ValueError("SH order must be provided")
        if self.sh_order not in [2, 4, 6, 8]:
            raise ValueError("SH order must be one of [2,4,6,8]")

    def get_state_dict(self):
        """Get a dictionary representation to store in the HDF file."""
        state_dict = super().get_state_dict()
        state_dict['sh_order'] = self.sh_order

        return state_dict

    def load_and_process_volume(self, dwi_image: nib.Nifti1Image,
                                gradient_table: GradientTable,
                                wm_mask_image: nib.Nifti1Image,
                                output_path: pathlib.Path) -> np.ndarray:
        """Process a volume for a fODF-SH dataset. Compute a response function,
         fit fODFs and return the corresponding SH coeffs.

        Parameters
        ----------
        dwi_image : nib.Nifti1Image
            Diffusion-weighted images (4D)
        gradient_table : dipy.io.gradients.GradientTable
            Dipy object that contains all bvals and bvecs.
        wm_mask_image : nib.Nifti1Image
            Binary white matter mask.
        output_path : str
            Path to the output folder.

        Returns
        -------
        output : np.ndarray
            The processed output volume.
        """

        # Don't provide a wm mask, instead rely on FA threshold
        frf = compute_ssst_frf(dwi_image, gradient_table)

        # Save frf to file
        np.savetxt(str(output_path.joinpath("frf.txt")), frf)

        n_peaks = 1  # Cannot use 0 peaks, so we use only 1
        return_sh = True

        # Computing fODF only inside WM mask
        pam = compute_fodf(dwi_image, gradient_table, frf, self.sh_order,
                           n_peaks, wm_mask_image, return_sh)

        output = pam.shm_coeff.astype(np.float32)
        return output


class TractoDatasetLoaderFODFPeaks(TractoDatasetCreatorGeneric):
    """Class containing all configuration options for creating a new fODF-peaks
    dataset."""

    def __init__(self, sh_order: int = None, n_peaks: int = None, *args,
                 **kwargs):
        """
        Parameters
        ----------
        sh_order : int
            The SH order used to fit the signal
        n_peaks : int
            The number of peaks to use as input to the model
        """
        super().__init__(*args, **kwargs)
        self.sh_order = sh_order
        self.n_peaks = n_peaks
        if self.sh_order is None:
            raise ValueError("SH order must be provided")
        if self.sh_order not in [2, 4, 6, 8]:
            raise ValueError("SH order must be one of [2,4,6,8]")

        if self.n_peaks is None:
            raise ValueError("n_peaks must be provided")
        if self.n_peaks not in [1, 2, 3]:
            raise ValueError("n_peaks must be one of [1,2,3]")

    def get_state_dict(self):
        """Get a dictionary representation to store in the HDF file."""
        state_dict = super().get_state_dict()
        state_dict['sh_order'] = self.sh_order
        state_dict['n_peaks'] = self.n_peaks

        return state_dict

    def load_and_process_volume(self, dwi_image: nib.Nifti1Image,
                                gradient_table: GradientTable,
                                wm_mask_image: nib.Nifti1Image,
                                output_path: pathlib.Path) -> np.ndarray:
        """Process a volume for a fODF-peaks dataset.

        Compute a response function, fit fODFs,
        extract the main peaks and return a 4D volume, where the last axis
        is each peak (3D vector) with its value (scalar), all flattened into
        a single dimension.

        Parameters
        ----------
        dwi_image : nib.Nifti1Image
            Diffusion-weighted images (4D)
        gradient_table : dipy.io.gradients.GradientTable
            Dipy object that contains all bvals and bvecs.
        wm_mask_image : nib.Nifti1Image
            Binary white matter mask.
        output_path : str
            Path to the output folder.

        Returns
        -------
        output : np.ndarray
            The processed output volume.
        """

        # Don't provide a wm mask, instead rely on FA threshold
        frf = compute_ssst_frf(dwi_image, gradient_table)

        # Save frf to file
        np.savetxt(str(output_path.joinpath("frf.txt")), frf)

        return_sh = False

        # Computing fODF only inside WM mask
        pam = compute_fodf(dwi_image, gradient_table, frf, self.sh_order,
                           self.n_peaks, wm_mask_image, return_sh)

        # Peaks directions are scaled by the normalized peaks values
        fodf_peaks_dirs = pam.peak_dirs.astype(np.float32)

        new_shape = wm_mask_image.shape + (-1,)
        output = fodf_peaks_dirs.reshape(new_shape)
        return output
