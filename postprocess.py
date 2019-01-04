# -*- coding: utf-8 -*-
"""
Created on Thu Aug 23 15:49:26 2018

@author: Pieter Roggemans
"""

'''
import sys
path = "/home/dpakhom1/dense_crf_python/"
sys.path.append(path)
import pydensecrf.densecrf as dcrf
from pydensecrf.utils import compute_unary, create_pairwise_bilateral, \
    create_pairwise_gaussian, softmax_to_unary
import skimage.io as io
'''

import logging
import os
import glob
import shutil
import concurrent.futures as futures

import numpy as np
import skimage
import skimage.morphology       # Needs to be imported explicitly as it is a submodule
from scipy import ndimage
#import pydensecrf.densecrf as dcrf
import rasterio as rio
import rasterio.features as rio_features
import rasterio.plot as rio_plot
import shapely as sh
import shapely.ops as sh_ops

import vector_helper as vh

def read_image(image_filepath: str):
    # Read input file and return data.
    with rio.open(image_filepath) as image_ds:
        # Read pixels
        image_data = image_ds.read()
        # Change from (channels, width, height) tot (width, height, channels)
        image_data = rio_plot.reshape_as_image(image_data)

    # Make sure the pixels values are between 0 and 1
    image_data = image_data / 255
    
    return image_data

def region_segmentation(predicted_mask,
                        thresshold_ok: float = 0.5):
    
    # ???
    elevation_map = skimage.filters.sobel(predicted_mask)
    
    # First apply some basic thressholds...
    markers = np.zeros_like(predicted_mask)
    markers[predicted_mask < thresshold_ok] = 1
    markers[predicted_mask >= thresshold_ok] = 2

    # Clean    
    segmentation = skimage.morphology.watershed(elevation_map, markers)   
    
    # Remove holes in the mask
    segmentation = ndimage.binary_fill_holes(segmentation - 1)
    
    return segmentation

def thresshold(mask, thresshold_ok: float = 0.5):
    mask[mask >= thresshold_ok] = 1
    mask[mask < thresshold_ok] = 0
    
    return mask

def test_postprocess_prediction(image_pred_filepath,
                           image_filepath: str,
                           output_dir: str,
                           input_mask_dir: str = None,
                           border_pixels_to_ignore: int = 0,
                           evaluate_mode: bool = False,
                           force: bool = False):
    base_filename = os.path.basename(image_pred_filepath)
        
    try:
        with open(f"c:\\tmp\\{base_filename}.txt", "w+") as testfile:
            testfile.write('test')
        return
    except:
        with open(f"c:\\tmp\\{base_filename}_exception.txt", "w+") as testfile:
            testfile.write('test_exception')
        raise
    #logger.error(f"Start postprocess for {image_pred_filepath}")
    
def postprocess_prediction(image_filepath: str,
                           output_dir: str,
                           image_pred_arr = None,
                           image_pred_filepath = None,
                           input_mask_dir: str = None,
                           border_pixels_to_ignore: int = 0,
                           evaluate_mode: bool = False,
                           force: bool = False):
    
    # Get a logger...
    logger = logging.getLogger(__name__)
    #logger.setLevel(logging.DEBUG)

    logger.debug(f"Start postprocess for {image_pred_filepath}")
    
    # Either the filepath to a temp file with the prediction or the data 
    # itself need to be provided
    if(image_pred_arr is None
       and image_pred_filepath is None):
        message = f"postprocess_prediction needs either image_pred_arr or image_pred_filepath, now both are None"
        logger.error(message)
        raise Exception(message)

    try:      
                                   
        # Prepare the filepath for the output
        input_image_dir, image_filename = os.path.split(image_filepath)
        image_filename_noext, image_ext = os.path.splitext(image_filename)
        
        if image_pred_arr is not None:
            image_pred_orig = image_pred_arr
        else:
            image_pred_orig = np.load(image_pred_filepath)
            os.remove(image_pred_filepath)
        
        # Check the number of channels of the output prediction
        # TODO: maybe param name should be clearer !!!
        n_channels = image_pred_orig.shape[2]
        if n_channels > 1:
            raise Exception(f"Not implemented: processing prediction output with multiple channels: {n_channels}")
                
        # Make the array 2 dimensial for the next algorithm. Is no problem if there
        # is only one channel
        image_pred_orig = image_pred_orig.reshape((image_pred_orig.shape[0], image_pred_orig.shape[1]))
        
        # Make the pixels at the borders of the prediction black so they are ignored
        if border_pixels_to_ignore and border_pixels_to_ignore > 0:
            image_pred_orig[0:border_pixels_to_ignore,:] = 0    # Left border
            image_pred_orig[-border_pixels_to_ignore:,:] = 0    # Right border
            image_pred_orig[:,0:border_pixels_to_ignore] = 0    # Top border
            image_pred_orig[:,-border_pixels_to_ignore:] = 0    # Bottom border
    
        # Check if the result is entirely black... if so no cleanup needed
        all_black = False
        thresshold_ok = 0.5
        image_pred = None
        if not np.any(image_pred_orig > thresshold_ok):
            logger.debug('Prediction is entirely black!')
            all_black = True
        else:
            # Cleanup the image so it becomes a clean 2 color one instead of grayscale
            logger.debug("Clean prediction")
            image_pred = region_segmentation(image_pred_orig, 
                                             thresshold_ok=thresshold_ok)
            if not np.any(image_pred > thresshold_ok):
                logger.info('Prediction became entirely black!')
                all_black = True

        # If not in evaluate mode and the prediction is all black, return
        if not evaluate_mode and all_black:
            logger.debug("All black prediction, no use saving this")
            return 
                    
        # Make sure the output dir exists...
        if not os.path.exists(output_dir):
            os.mkdir(output_dir)
    
        # Convert the output image to uint [0-255] instead of float [0,1]
        if image_pred is None:
            image_pred = thresshold(image_pred_orig, thresshold_ok=thresshold_ok)
        image_pred_uint8 = (image_pred * 255).astype(np.uint8)
            
        # If in evaluate mode, put a prefix in the file name
        pred_prefix_str = ''
        if evaluate_mode:
            
            def jaccard_similarity(im1, im2):
                if im1.shape != im2.shape:
                    message = f"Shape mismatch: input have different shape: im1: {im1.shape}, im2: {im2.shape}"
                    logger.critical(message)
                    raise ValueError(message)
    
                intersection = np.logical_and(im1, im2)
                union = np.logical_or(im1, im2)
    
                sum_union = float(union.sum())
                if sum_union == 0.0:
                    # If 0 positive pixels in union: perfect prediction, so 1
                    return 1
                else:
                    sum_intersect = intersection.sum()
                    return sum_intersect/sum_union
    
            # If there is a mask dir specified... use the groundtruth mask
            if input_mask_dir and os.path.exists(input_mask_dir):
                # Read mask file and get all needed info from it...
                mask_filepath = image_filepath.replace(input_image_dir,
                                                       input_mask_dir)
                # Check if this file exists, if not, look for similar files
                if not os.path.exists(mask_filepath):
                    mask_filepath_noext = os.path.splitext(mask_filepath)[0]
                    files = glob.glob(mask_filepath_noext + '*')
                    if len(files) == 1:
                        mask_filepath = files[0]
                    else:
                        message = f"Error finding mask file with {mask_filepath_noext + '*'}: {len(files)} mask(s) found"
                        logger.error(message)
                        raise Exception(message)
    
                with rio.open(mask_filepath) as mask_ds:
                    # Read pixels
                    mask_arr = mask_ds.read(1)
    
                # Make the pixels at the borders of the mask black so they are 
                # ignored in the comparison
                if border_pixels_to_ignore and border_pixels_to_ignore > 0:
                    mask_arr[0:border_pixels_to_ignore,:] = 0    # Left border
                    mask_arr[-border_pixels_to_ignore:,:] = 0    # Right border
                    mask_arr[:,0:border_pixels_to_ignore] = 0    # Top border
                    mask_arr[:,-border_pixels_to_ignore:] = 0    # Bottom border
                    
                #similarity = jaccard_similarity(mask_arr, image_pred)
                # Use accuracy as similarity... is more practical than jaccard
                similarity = np.equal(mask_arr, image_pred_uint8).sum()/image_pred_uint8.size
                pred_prefix_str = f"{similarity:0.3f}_"
                
                # Copy mask file if the file doesn't exist yet
                mask_copy_dest_filepath = f"{output_dir}{os.sep}{pred_prefix_str}{image_filename_noext}_mask.tif"
                if not os.path.exists(mask_copy_dest_filepath):
                    shutil.copyfile(mask_filepath, mask_copy_dest_filepath)
    
            else:
                # If all_black, no need to calculate again
                if all_black: 
                    pct_black = 1
                else:
                    # Calculate percentage black pixels
                    pct_black = 1 - (image_pred_uint8.sum()/255)/image_pred_uint8.size
                
                # If the result after segmentation is all black, set all_black
                if pct_black == 1:
                    # Force the prefix to be really high so it is clear they are entirely black
                    pred_prefix_str = "1.001_"
                    all_black = True
                else:
                    pred_prefix_str = f"{pct_black:0.3f}_"
    
                # If there are few white pixels, don't save it,
                # because we are in evaluetion mode anyway...
                #if similarity >= 0.95:
                    #continue
          
            # Copy the input image if it doesn't exist yet in output path
            image_copy_dest_filepath = f"{output_dir}{os.sep}{pred_prefix_str}{image_filename_noext}{image_ext}"
            if not os.path.exists(image_copy_dest_filepath):
                shutil.copyfile(image_filepath, image_copy_dest_filepath)

            # If all_black, we are ready now
            if all_black:
                logger.debug("All black prediction, no use proceding")
                return 
        
        # Read some properties of the input image to use them for the output image
        # First try using the input image
        with rio.open(image_filepath) as src_ds:
            src_image_width = src_ds.profile['width']
            src_image_height = src_ds.profile['height']
            src_image_bounds = src_ds.bounds
            src_image_crs = src_ds.profile['crs']
            src_image_transform = src_ds.transform
    
        # If the input image didn't contain proper info, try using the mask image
        if src_image_transform[0] == 0 and input_mask_dir:
            # Create mask filename and read
            mask_filepath = image_filepath.replace(input_image_dir,
                                                   input_mask_dir)        
            with rio.open(mask_filepath) as src_ds:
                src_image_width = src_ds.profile['width']
                src_image_height = src_ds.profile['height']
                src_image_bounds = src_ds.bounds
                src_image_crs = src_ds.profile['crs']
                src_image_transform = src_ds.transform
        
        # If still no decent metadata found, write warning
        if src_image_transform[0] == 0:
            # If in evaluate mode... warning is enough, otherwise error!
            message = f"No transform found in {image_filepath}: {src_image_transform}"        
            if evaluate_mode:
                logger.warn(message)
            else:
                logger.error(message)
                raise Exception(message)          
                
        # Now write original prediction (as uint8) to file
        logger.debug("Save original prediction")
        image_pred_orig = (image_pred_orig * 255).astype(np.uint8)
        image_pred_orig_filepath = f"{output_dir}{os.sep}{pred_prefix_str}{image_filename_noext}_pred.tif"
        with rio.open(image_pred_orig_filepath, 'w', driver='GTiff', compress='lzw',
                      height=src_image_height, width=src_image_width, 
                      count=1, dtype=rio.uint8, crs=src_image_crs, transform=src_image_transform) as dst:
            dst.write(image_pred_orig.astype(rio.uint8), 1)
            
        # Polygonize result
        # Returns a list of tupples with (geometry, value)
        shapes = rio_features.shapes(image_pred_uint8.astype(rio.uint8),
                                     mask=image_pred_uint8.astype(rio.uint8),
                                     transform=src_image_transform)
    
        # Convert shapes to shapely geoms 
        geoms = []
        for shape in list(shapes):
            geom, value = shape
            geom_sh = sh.geometry.shape(geom)
            geoms.append(geom_sh)   
        
        # If not in evaluate mode, write the original geoms to wkt file
        if evaluate_mode is False:
            logger.debug('Before writing orig geom file')
            geom_filepath = f"{output_dir}{os.sep}{pred_prefix_str}{image_filename_noext}_pred_cleaned.geojson"
            
            x_pixelsize = get_pixelsize_x(src_image_transform)
            y_pixelsize = get_pixelsize_y(src_image_transform)
            border_xmin = src_image_bounds.left + border_pixels_to_ignore * x_pixelsize
            border_ymin = src_image_bounds.top + border_pixels_to_ignore * y_pixelsize
            border_xmax = src_image_bounds.right - border_pixels_to_ignore * x_pixelsize
            border_ymax = src_image_bounds.bottom - border_pixels_to_ignore * y_pixelsize
            vh.write_segmented_geoms(geoms, geom_filepath, src_image_crs,
                                     border_xmin, border_ymin, border_xmax, border_ymax)
        else:
            # For easier evaluation, write some more cleaned versions to disk
            '''
            # Write the standard cleaned output to file
            logger.debug("Save cleaned prediction")
            image_pred_cleaned_filepath = f"{output_dir}{os.sep}{pred_prefix_str}{image_filename_noext}_pred_cleaned.tif"
            with rio.open(image_pred_cleaned_filepath, 'w', driver='GTiff', compress='lzw',
                          height=src_image_height, width=src_image_width, 
                          count=1, dtype=rio.uint8, crs=src_image_crs, transform=src_image_transform) as dst:
                dst.write(image_pred_uint8.astype(rio.uint8), 1)
            '''
            
            # If the input image contained a tranform, also create an image 
            # based on the simplified vectors
            if(src_image_transform[0] != 0 
               and len(geoms) > 0):
                # Simplify geoms
                geoms_simpl = []
                geoms_simpl_vis = []
                for geom in geoms:
                    '''
                    # The simplify of shapely uses the deuter-pecker algo
                    # preserve_topology is slower bu makes sure no polygons are removed
                    geom_simpl = geom.simplify(1.5, preserve_topology=True)
                    if not geom_simpl.is_empty:
                        geoms_simpl.append(geom_simpl)
                    '''
                    
                    # Also do the simplify with Visvalingam algo
                    # -> seems to give better results for this application
                    geom_simpl_vis = vh.simplify_visval(geom, 10)                       
                    if geom_simpl_vis is not None:
                        geoms_simpl_vis.append(geom_simpl_vis)
                
                '''
                # Write simplified wkt result to raster for comparing. 
                if len(geoms_simpl) > 0:
                    # TODO: doesn't support multiple classes
                    logger.debug('Before writing simpl rasterized file')
                    image_pred_simpl_filepath = f"{output_dir}{os.sep}{pred_prefix_str}{image_filename_noext}_pred_cleaned_simpl.tif"
                    with rio.open(image_pred_simpl_filepath, 'w', driver='GTiff', compress='lzw',
                                  height=src_image_height, width=src_image_width, 
                                  count=1, dtype=rio.uint8, crs=src_image_crs, transform=src_image_transform) as dst:
                        # this is where we create a generator of geom, value pairs to use in rasterizing
                #            shapes = ((geom,value) for geom, value in zip(counties.geometry, counties.LSAD_NUM))
                        logger.debug('Before rasterize')
                        if geoms_simpl:               
                            # Now rasterize!
                            burned = rio_features.rasterize(shapes=geoms_simpl, 
                                                            out_shape=(src_image_height, 
                                                                       src_image_width),
                                                            fill=0, default_value=255,
                                                            dtype=rio.uint8,
                                                            transform=src_image_transform)
                #            logger.debug(burned)
                            dst.write(burned, 1)
                '''
                
                # Write simplified wkt result to raster for comparing. Use the same
                if len(geoms_simpl_vis) > 0:
                    # file profile as created before for writing the raw prediction result
                    # TODO: doesn't support multiple classes
                    logger.debug('Before writing simpl with visvangali algo rasterized file')
                    image_pred_simpl_filepath = f"{output_dir}{os.sep}{pred_prefix_str}{image_filename_noext}_pred_cleaned_simpl_vis.tif"
                    with rio.open(image_pred_simpl_filepath, 'w', driver='GTiff', compress='lzw',
                                  height=src_image_height, width=src_image_width, 
                                  count=1, dtype=rio.uint8, crs=src_image_crs, transform=src_image_transform) as dst:
                        # this is where we create a generator of geom, value pairs to use in rasterizing
                #            shapes = ((geom,value) for geom, value in zip(counties.geometry, counties.LSAD_NUM))
                        logger.debug('Before rasterize')
                        if geoms_simpl:               
                            # Now rasterize!
                            burned = rio_features.rasterize(shapes=geoms_simpl_vis, 
                                                            out_shape=(src_image_height, 
                                                                       src_image_width),
                                                            fill=0, default_value=255,
                                                            dtype=rio.uint8,
                                                            transform=src_image_transform)
                #            logger.debug(burned)
                            dst.write(burned, 1)
    
    except:
        logger.error(f"Exception postprocessing prediction for {image_filepath}\n: file {image_pred_filepath}!!!")
        raise
        
'''
Dense crf didn't seem to work very good... so dropped it.

Usage eg.:
    #    image_pred = pp.dense_crf(image=image_arr, mask=image_pred)
    #    image_pred = (image_pred * 255).astype(np.uint8)
    #    image_pred = pp.thresshold(image_pred)
    #    image_pred = np.delete(image_pred, obj=0, axis=2)
    
def dense_crf(image, mask):
    height = mask.shape[0]
    width = mask.shape[1]

    mask = np.expand_dims(mask, 0)
    mask = np.append(1 - mask, mask, axis=0)

    d = dcrf.DenseCRF2D(width, height, 2)
    U = -np.log(mask)
    U = U.reshape((2, -1))
    U = np.ascontiguousarray(U)
    img = np.ascontiguousarray(image, dtype=np.uint8)

    d.setUnaryEnergy(U)

#    d.addPairwiseGaussian(sxy=20, compat=3)
#    d.addPairwiseBilateral(sxy=30, srgb=20, rgbim=img, compat=10)
    d.addPairwiseGaussian(sxy=3, compat=3)
    d.addPairwiseBilateral(sxy=3, srgb=3, rgbim=img, compat=3)

    Q = d.inference(2)
    Q = np.argmax(np.array(Q), axis=0).reshape((height, width))

    return np.array(Q)
'''
'''
def postprocess():

    image = train_image

    softmax = final_probabilities.squeeze()

    softmax = processed_probabilities.transpose((2, 0, 1))

    # The input should be the negative of the logarithm of probability values
    # Look up the definition of the softmax_to_unary for more information
    unary = softmax_to_unary(processed_probabilities)

    # The inputs should be C-continious -- we are using Cython wrapper
    unary = np.ascontiguousarray(unary)

    d = dcrf.DenseCRF(image.shape[0] * image.shape[1], 2)

    d.setUnaryEnergy(unary)

    # This potential penalizes small pieces of segmentation that are
    # spatially isolated -- enforces more spatially consistent segmentations
    feats = create_pairwise_gaussian(sdims=(10, 10), shape=image.shape[:2])

    d.addPairwiseEnergy(feats, compat=3,
                        kernel=dcrf.DIAG_KERNEL,
                        normalization=dcrf.NORMALIZE_SYMMETRIC)

    # This creates the color-dependent features --
    # because the segmentation that we get from CNN are too coarse
    # and we can use local color features to refine them
    feats = create_pairwise_bilateral(sdims=(50, 50), schan=(20, 20, 20),
                                       img=image, chdim=2)

    d.addPairwiseEnergy(feats, compat=10,
                         kernel=dcrf.DIAG_KERNEL,
                         normalization=dcrf.NORMALIZE_SYMMETRIC)
    Q = d.inference(5)

    res = np.argmax(Q, axis=0).reshape((image.shape[0], image.shape[1]))

    cmap = plt.get_cmap('bwr')

    f, (ax1, ax2) = plt.subplots(1, 2, sharey=True)
    ax1.imshow(res, vmax=1.5, vmin=-0.4, cmap=cmap)
    ax1.set_title('Segmentation with CRF post-processing')
    probability_graph = ax2.imshow(np.dstack((train_annotation,)*3)*100)
    ax2.set_title('Ground-Truth Annotation')
    plt.show()
'''

#
# Helpers for working with Affine objects...                    
#
def get_pixelsize_x(transform):
    return transform[0]
    
def get_pixelsize_y(transform):
    return -transform[4]

# If the script is ran directly...
if __name__ == '__main__':
    # Get a logger...
    logger = logging.getLogger(__name__)
    #logger.setLevel(logging.DEBUG)
    postprocess_future_list = []
    with futures.ProcessPoolExecutor(6) as procpool:

        # a loop...
        for i in range(0, 5):
            # Put parameters for the postprocessing function in a dict
            kwargs = {'image_pred_filepath': f'test_{i}',
                      'image_filepath': 'test',
                      'output_dir': 'test',
                      'input_mask_dir': 'test',
                      'border_pixels_to_ignore': 'test',
                      'evaluate_mode': False,
                      'force': False}
            postprocess_future_list.append(procpool.submit(test_postprocess_prediction, 
                                           **kwargs))
        # Wait for all postprocessing threads to be finished
        res = futures.wait(postprocess_future_list)
        #logger.info(res)
        