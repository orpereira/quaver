import os
# from astropy.utils.data import conf
# conf.remote_timeout = 30  # Increase timeout to 30 seconds
# os.environ["ASTROPY_COORDS_SESAME_URL"] = "http://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxp/~?%s"

import http
#import lightkurve as lk
from lightkurve import search_tesscut
from lightkurve import DesignMatrix
from lightkurve import DesignMatrixCollection
from lightkurve import RegressionCorrector
from lightkurve import LightCurve

# import polygon
from skimage.draw import polygon

import numpy as np
import re
import sys
from copy import deepcopy
import ast

import matplotlib.pyplot as plt
from matplotlib import gridspec
from matplotlib import patches

from astroquery.simbad import Simbad
from astroquery.skyview import SkyView
from astropy.coordinates import get_icrs_coordinates
from astropy.coordinates.name_resolve import NameResolveError
from astropy.coordinates import SkyCoord
from astropy.wcs import *
from astropy import units as u
import astropy.io.fits as pyfits

#################
#################

#THE FOLLOWING STATEMENTS MAY BE NEEDED IF RUNNING IN WINDOWS LINUX ENVIRONMENT:
#(NOTE: adding these may cause a Tkinter deprecation warning, but should not affect performance.)

#import matplotlib
#import tkinter
#matplotlib.use("TkAgg")

####################
###################


##################  TUNABLE PARAMETERS  ##########################

#For which method would you like to see detailed plots during reduction for each sector? All methods will be read out at the end.
#  1 = simple reduction using Principle Component Analysis
#  2 = simple hybrid reduction
#  3 = full hybrid reduction

primary_correction_method = 3

#Size of the TPF postage stamp to download and use for extraction and detrending.
tpf_width_height = 50

#Number of PCA Components in the Hybrid method and simple PCA correction.
additive_pca_num = 3
multiplicative_pca_num = 3
pca_only_num = 3

#Lowest DSS contour level, as fraction of peak brightness in DSS image.
#(For fields with bright stars, the default lowest level of 0.4 may be too high to see your faint source)
#This number must be less than 0.65.
lowest_dss_contour = 0.4

flux_scaling = 1e-1

#Acceptable threshold for systematics in additive components:
sys_threshold = 0.2

#Maximum number of cadence-mask regions allowed:
max_masked_regions = 5 #set maximum number of regions of the light curve that can be masked out.

#Which cadence of the TESSCut file is used for the aperture selection panel
#(It is best to avoid the first or last cadences as they are often hard to see due to systematics)
plot_index = 200

#Threshold, in multiples of sigma times the median of the flux across the entire TPF,
#that divides bright from faint pixels in the calculation of principal components.
bf_threshold = 1.5

#Whether Lightkurve should attempt to propagate the flux errors during the matrix correction.
#Note that this can significantly increase the runtime. Without this, errors in output will be
#photometric flux errors reported from the TESSCut and propagated only from extraction.
prop_error_flag = False

# ADDED output directory for quaver outputs to be saved
out_dir = '/Users/oliviapereira/Documents/MEGA/quaver_outputs_stars/'

# ADDED directory for saving/loading aperture and cadence masking selections 
aperture_dir = '/Users/oliviapereira/Documents/MEGA/quaver_apertures/'
mask_dir = '/Users/oliviapereira/Documents/MEGA/quaver_masks/'

# toggle showing plots or just saving them
show_plots = True

############################################

############################################
#Define function to record the positions of clicks in the pixel array image for the extraction mask.
def onclick(event):

    global ix,iy
    ix,iy = int(round(event.xdata)),int(round(event.ydata))

    global row_col_coords

    if (iy,ix) in row_col_coords:
        row_col_coords.remove((iy,ix))
        plt.plot(ix,iy,marker="x",color='red',markersize=9)
        fig.canvas.draw()

        print('removing'+str((ix,iy)))
    else:
        row_col_coords.append((iy,ix))
        plt.plot(ix,iy,marker=u"$\u2713$",color='limegreen',markersize=9)
        fig.canvas.draw()

        print('adding'+str((ix,iy)))

#############################################


############################################
#Define function to record the X-positions of the cadences to mask out if needed.
def onclick_cm(event):

    global ix_cm
    ix_cm = int(round(event.xdata))

    global masked_cadence_limits
    masked_cadence_limits.append(ix_cm)
    print(ix_cm)

    plt.axvline(x=ix_cm,color='red')
    fig_cm.canvas.draw()

#############################################

############################################
#Define function to resolve the target name or coordinates to ICRS coordinates.
#This function will first try to use the astropy name resolver, and if it fails, it will use astroquery.simbad.
def resolve_target(target):
    try:
        return get_icrs_coordinates(target)
    except NameResolveError:
        print("Astropy name resolver failed, trying astroquery.simbad...")
        result = Simbad.query_object(target)
        if result is not None:
            ra = result['RA'][0]
            dec = result['DEC'][0]
            # Convert to decimal degrees
            coord = SkyCoord(f"{ra} {dec}", unit=(u.hourangle, u.deg), frame='icrs')
            return coord
        else:
            raise NameResolveError(f"Could not resolve target '{target}' with Simbad.")

#############################################
#Define function for stitching the sectors together once corrected:

def lc_stitch(unstitched_lc):

    for j in range(0,len(unstitched_lc)):
        if j!=0:
            sector = str(j+1)

        lc = unstitched_lc[j]

        t = lc[:,0]
        f = lc[:,1]
        err = lc[:,2]


        if j == 0:

            full_lc_time = t
            full_lc_flux = f
            full_lc_err= err

        else:

            first_flux = np.mean(f[:10])
            last_flux = np.mean(full_lc_flux[-10:])

            scale_factor= first_flux - last_flux

            if scale_factor > 0:

                scaled_flux = f - abs(scale_factor)

            if scale_factor < 0:

                scaled_flux = f + abs(scale_factor)

            full_lc_time = np.append(full_lc_time,t)
            full_lc_flux = np.append(full_lc_flux,scaled_flux)
            full_lc_err = np.append(full_lc_err,err)

    return full_lc_time,full_lc_flux,full_lc_err

#############################################

#############################################
#Define function to remove single-cadence jumps of greater or lesser than 1% of the flux on either side.
def remove_jumps(t,f,err):

    jump_idx = []
    for i in range(0,len(t)-1):
        
        if i !=0 and i != len(f)-1 and f[i] > (0.01 * f[i-1]+f[i-1]) and f[i] > (0.01 * f[i+1] + f[i+1]):

            jump_idx.append(i)

    for i in range(0,len(t)-1):

        if i !=0 and i != len(f)-1 and f[i] < (f[i-1] - 0.01 * f[i-1]) and f[i] < (f[i+1]-0.01 * f[i+1]):

            jump_idx.append(i)
            
    count = 0
    for idx in jump_idx: 
        
        t = np.delete(t,i - count)
        f = np.delete(f,i - count)
        err = np.delete(err,i - count)
        count += 1

    return t,f,err
##############################################

######## BEGIN MAIN PROGRAM #################
#Define target and obtain DSS image from common name or coordinates.
try :
    target = input('Target Common Name: ')
    target_coordinates = target
    source_coordinates = resolve_target(target)       #this requires that SIMBAD be up and working...
    print(source_coordinates)
    print("\n")

except NameResolveError:
    print("\n"+"Could not find target by name provided. Try Sky Coordinates.\n")
    print("Input as ICRS: RA,Dec  (in Decimal Degrees, with no space)")

    input_coord_string = input('RA,Dec: ')
    input_coord_split = re.split("\s|[,]|[,\s]",input_coord_string)

    ra = float(input_coord_split[0])
    dec = float(input_coord_split[1])

    source_coordinates = SkyCoord(ra,dec,frame='icrs',unit='deg')

    target = input('Desired object name for output files: ')
    target_coordinates = str(ra)+" "+str(dec)

    print(source_coordinates)
    print("\n")



dss_image = SkyView.get_images(position=source_coordinates,survey='DSS',pixels=str(400))
wcs_dss = WCS(dss_image[0][0].header)
dss_pixmin = np.min(dss_image[0][0].data)
dss_pixmax = np.max(dss_image[0][0].data)
dss_pixmean = np.mean(dss_image[0][0].data)

dss_head = dss_image[0][0].header
dss_ra = dss_head['CRVAL1']
dss_dec = dss_head['CRVAL2']


#Retrieve the available tesscut data for FFI-only targets.
sector_data = search_tesscut(target_coordinates)
num_obs_sectors = len(sector_data)

if num_obs_sectors == 0:
    print("This object has not been observed by TESS.")

    sys.exit()


print(sector_data)
print('\n')
print('Table of Cycles by Sector:')
print('Cycle 1: Sectors 1-13')
print('Cycle 2: Sectors 14-26')
print('Cycle 3: Sectors 27-39')
print('Cycle 4: Sectors 40-55')
print('Cycle 5: Sectors 56-69')
print('Cycle 6: Sectors 70-83')
print('Cycle 7: Sectors 84-96')

#Set cycle of interest, while making sure the chosen cycle corresponds to actual observed sectors:

check_cycle = False

while check_cycle == False:

    cycle = int(input('Enter Cycle: '))

    first_sectors = [1,14,27,40,56,70,84]
    last_sectors = [13,26,39,55,69,83,96]

    if cycle == 1:
        first_sector = first_sectors[0]
        last_sector = last_sectors[0]
    elif cycle ==2:
        first_sector = first_sectors[1]
        last_sector = last_sectors[1]
    elif cycle ==3:
        first_sector = first_sectors[2]
        last_sector = last_sectors[2]
    elif cycle==4:
        first_sector = first_sectors[3]
        last_sector = last_sectors[3]
    elif cycle==5:
        first_sector = first_sectors[4]
        last_sector = last_sectors[4]
    elif cycle==6:
        first_sector = first_sectors[5]
        last_sector = last_sectors[5]
    elif cycle==7:
            first_sector = first_sectors[6]
            last_sector = last_sectors[6]

    else:
        print('Invalid Cycle Number')
        sys.exit()

    list_observed_sectors = []
    list_observed_sectors_in_cycle = []
    list_sectordata_index_in_cycle = []

    for i in range(0,len(sector_data)):

        sector_number = int(sector_data[i].mission[0][12:14])       #This will need to change, Y2K style, if TESS ever has more than 100 sectors.
        list_observed_sectors.append(sector_number)

        if sector_number >= first_sector and sector_number <= last_sector:

            list_observed_sectors_in_cycle.append(sector_number)
            list_sectordata_index_in_cycle.append(i)

    check_cycle = any(i>=first_sector and i<=last_sector for i in list_observed_sectors)
    if check_cycle == False:
        print('Selected cycle does not correspond to any observed sectors. Try again.')




if num_obs_sectors == 0:
    print("This object has not been observed by TESS.")

    sys.exit()

#If object is observed by TESS and specified Cycle makes sense, begin aperture
#selection and extraction!

unstitched_lc_pca = []
unstitched_lc_simple_hyb = []
unstitched_lc_full_hyb = []

for i in range(0,len(list_sectordata_index_in_cycle)):

    try:

        tpf = sector_data[list_sectordata_index_in_cycle[i]].download(cutout_size=(tpf_width_height, tpf_width_height)) #gets earliest sector

        sector_number = tpf.get_header()['SECTOR']
        sec = str(sector_number)
        ccd = tpf.get_header()['CCD']
        cam = tpf.get_header()['CAMERA']

        print("Generating pixel map for sector "+sec+".\n")

        #Check that this object is actually on silicon and getting data (not always the case just because TESSCut says so).
        #By making a light curve from a dummy aperture of the middle 5x5 square and seeing if its mean flux is zero.

        aper_dummy = np.zeros(tpf[0].shape[1:], dtype=bool) #blank
        aper_dummy[int(tpf_width_height/2-3):int(tpf_width_height/2+3),int(tpf_width_height/2-3):int(tpf_width_height/2+3)] = True
        lc_dummy = tpf.to_lightcurve(aperture_mask=aper_dummy)

        if np.mean(lc_dummy.flux) == 0:
            print("This object is not actually on silicon.")
            sys.ext()

        else:

            hdu = tpf.get_header(ext=2)

            #Get WCS information and flux stats of the TPF image.
            tpf_wcs = WCS(tpf.get_header(ext=2))

            pixmin = np.min(tpf.flux[plot_index]).value
            pixmax = np.max(tpf.flux[plot_index]).value
            pixmean = np.mean(tpf.flux[plot_index]).value

            temp_min = float(pixmin)
            # print(temp_min)
            temp_max = float(flux_scaling*pixmax+pixmean)
            #temp_max = pixmax
            # print(temp_max)

            #Create a blank boolean array for the aperture, which will turn to TRUE when pixels are selected.

            aper = np.zeros(tpf[0].shape[1:], dtype=bool) #blank
            aper_mod = aper.copy()       #For the source aperture
            aper_buffer = aper.copy()    #For the source aperture plus a buffer region to exclude from both additive and mult. regressors

            aper_width = tpf[0].shape[1]

            # check if the aperture file exists and load it if it does
            aperture_file = os.path.join(aperture_dir, f"{target}_sector{sec}_aperture.txt")
            if os.path.exists(aperture_file):
                # Ask whether the user wants to load an existing aperture or define a new one.
                load_aperture = input("Load existing aperture from .txt file? (Y/N): ")
                if load_aperture.strip().upper() == 'Y':
                    print(f"Loading aperture from {aperture_file}")
                    with open(aperture_file, 'r') as f:
                        row_col_coords = [tuple(map(int, line.strip().split(','))) for line in f.readlines()]
                    aper_mod = np.zeros(tpf[0].shape[1:], dtype=bool)
                    for y, x in row_col_coords:
                        aper_mod[y, x] = True
                else: 
                    if lowest_dss_contour == 0.4:
                        dss_levels = [0.4*dss_pixmax,0.5*dss_pixmax,0.75*dss_pixmax]
                    elif lowest_dss_contour < 0.4:
                        dss_levels = [lowest_dss_contour*dss_pixmax,0.4*dss_pixmax,0.5*dss_pixmax,0.75*dss_pixmax]
                    elif lowest_dss_contour > 0.4:
                        dss_levels = [lowest_dss_contour*dss_pixmax,0.65*dss_pixmax,0.85*dss_pixmax,0.9*dss_pixmax,0.95*dss_pixmax]

                    fig = plt.figure(figsize=(8,8))
                    ax = fig.add_subplot(111,projection=tpf_wcs)
                    # ax.imshow(tpf.flux[plot_index].value,vmin=pixmin,vmax=1e-3*pixmax+pixmean)
                    ax.imshow(tpf.flux[plot_index].value, vmin=temp_min,vmax=temp_max)
                    ax.contour(dss_image[0][0].data,transform=ax.get_transform(wcs_dss),levels=dss_levels,colors='white',alpha=0.7)
                    ax.scatter(aper_width/2.0,aper_width/2.0,marker='x',color='k',s=8)

                    ax.set_xlim(-0.5,aper_width-0.5)  #This section is needed to fix the stupid plotting issue in Python 3.
                    ax.set_ylim(-0.5,aper_width-0.5)

                    plt.title('Define extraction pixels:')
                    row_col_coords = []
                    cid = fig.canvas.mpl_connect('button_press_event',onclick)
                    
                    plt.show()
                    plt.close(fig)

                    auto_fill = input("Auto-fill the contour and select all pixels within the border? (Y/N): ")
                    if auto_fill.strip().upper() == 'Y' and len(row_col_coords) > 2:
                        # Use skimage.draw.polygon to fill the region
                        rows, cols = zip(*row_col_coords)
                        rr, cc = polygon(rows, cols, aper_mod.shape)
                        aper_mod[rr, cc] = True
                        print(f"Auto-filled {len(rr)} pixels inside the contour.")

                        # edit row_col_coords to match the filled region
                        row_col_coords = [(r, c) for r, c in zip(rr, cc)]
                        
                    else:
                        for i in range(0, len(row_col_coords)):
                            aper_mod[row_col_coords[i]] = True

                    fig.canvas.mpl_disconnect(cid)

                    # ask whether the selected aperture should be saved to a .txt file for later loading
                    save_aperture = input("Save the selected aperture to a .txt file for later use? (Y/N): ")
                    if save_aperture.strip().upper() == 'Y':
                        # create the output directory if it doesn't exist
                        if not os.path.exists(aperture_dir):
                            os.makedirs(aperture_dir)

                        # save the aperture coordinates to a .txt file
                        aperture_file = os.path.join(aperture_dir, f"{target}_sector{sec}_aperture.txt")
                        with open(aperture_file, 'w') as f:
                            for coord in row_col_coords:
                                f.write(f"{coord[0]},{coord[1]}\n")
                        print(f"Aperture saved to {aperture_file}")

            #Plot the TPF image and the DSS contours together, to help with aperture selection, along with the starter aperture.
            else: 
                if lowest_dss_contour == 0.4:
                    dss_levels = [0.4*dss_pixmax,0.5*dss_pixmax,0.75*dss_pixmax]
                elif lowest_dss_contour < 0.4:
                    dss_levels = [lowest_dss_contour*dss_pixmax,0.4*dss_pixmax,0.5*dss_pixmax,0.75*dss_pixmax]
                elif lowest_dss_contour > 0.4:
                    dss_levels = [lowest_dss_contour*dss_pixmax,0.65*dss_pixmax,0.85*dss_pixmax,0.9*dss_pixmax,0.95*dss_pixmax]

                fig = plt.figure(figsize=(8,8))
                ax = fig.add_subplot(111,projection=tpf_wcs)
                # ax.imshow(tpf.flux[plot_index].value,vmin=pixmin,vmax=1e-3*pixmax+pixmean)
                ax.imshow(tpf.flux[plot_index].value, vmin=temp_min,vmax=temp_max)
                ax.contour(dss_image[0][0].data,transform=ax.get_transform(wcs_dss),levels=dss_levels,colors='white',alpha=0.7)
                ax.scatter(aper_width/2.0,aper_width/2.0,marker='x',color='k',s=8)

                ax.set_xlim(-0.5,aper_width-0.5)  #This section is needed to fix the stupid plotting issue in Python 3.
                ax.set_ylim(-0.5,aper_width-0.5)

                plt.title('Define extraction pixels:')
                row_col_coords = []
                cid = fig.canvas.mpl_connect('button_press_event',onclick)
                
                plt.show()
                plt.close(fig)

                auto_fill = input("Auto-fill the contour and select all pixels within the border? (Y/N): ")
                if auto_fill.strip().upper() == 'Y' and len(row_col_coords) > 2:
                    # Use skimage.draw.polygon to fill the region
                    rows, cols = zip(*row_col_coords)
                    rr, cc = polygon(rows, cols, aper_mod.shape)
                    aper_mod[rr, cc] = True
                    print(f"Auto-filled {len(rr)} pixels inside the contour.")
                else:
                    for i in range(0, len(row_col_coords)):
                        aper_mod[row_col_coords[i]] = True

                fig.canvas.mpl_disconnect(cid)

                # ask whether the selected aperture should be saved to a .txt file for later loading
                save_aperture = input("Save the selected aperture to a .txt file for later use? (Y/N): ")
                if save_aperture.strip().upper() == 'Y':
                    # create the output directory if it doesn't exist
                    if not os.path.exists(aperture_dir):
                        os.makedirs(aperture_dir)

                    # save the aperture coordinates to a .txt file
                    aperture_file = os.path.join(aperture_dir, f"{target}_sector{sec}_aperture.txt")
                    with open(aperture_file, 'w') as f:
                        for coord in row_col_coords:
                            f.write(f"{coord[0]},{coord[1]}\n")
                    print(f"Aperture saved to {aperture_file}")

            buffer_pixels = []      #Define the buffer pixel region.

            if len(row_col_coords) == 0:
                print('No mask selected; skipping this Sector.')

            else:

                for i in range(0,len(row_col_coords)):

                    aper_mod[row_col_coords[i]] = True

                    row_same_up_column = (row_col_coords[i][0],row_col_coords[i][1]+1)
                    row_same_down_column = (row_col_coords[i][0],row_col_coords[i][1]-1)
                    column_same_down_row = (row_col_coords[i][0]-1,row_col_coords[i][1])
                    column_same_up_row = (row_col_coords[i][0]+1,row_col_coords[i][1])

                    bottom_left_corner = (row_col_coords[i][0]-1,row_col_coords[i][1]-1)
                    top_right_corner = (row_col_coords[i][0]+1,row_col_coords[i][1]+1)
                    top_left_corner = (row_col_coords[i][0]+1,row_col_coords[i][1]-1)
                    bottom_right_corner = (row_col_coords[i][0]-1,row_col_coords[i][1]+1)

                    buffer_line = (row_same_up_column,row_same_down_column,column_same_up_row,column_same_down_row,top_left_corner,top_right_corner,bottom_left_corner,bottom_right_corner)
                    buffer_pixels.append(buffer_line)

                    for coord_set in buffer_line:
                            aper_buffer[coord_set[0],coord_set[1]]=True



                #Create a mask that finds all of the bright, source-containing regions of the TPF.
                allbright_mask = tpf.create_threshold_mask(threshold=bf_threshold,reference_pixel=None)
                allfaint_mask = ~allbright_mask

                allbright_mask &= ~aper_buffer
                allfaint_mask &= ~aper_buffer

                #Remove any empty flux arrays from the downloaded TPF before we even get started:

                boolean_orignans = []

                for i in range(0,len(tpf.flux)):

                    if np.sum(tpf.flux[i] == 0) or np.isnan(np.sum(tpf.flux[i])) == True:

                        nanflag = True

                    else:

                        nanflag = False

                    boolean_orignans.append(nanflag)

                boolean_orignans_array = np.array(boolean_orignans)
                tpf = tpf[~boolean_orignans_array]

                #Get the additive background first:

                additive_hybrid_pcas = additive_pca_num

                additive_bkg = DesignMatrix(tpf.flux[:, allfaint_mask]).pca(additive_hybrid_pcas)
                additive_bkg_and_constant = additive_bkg.append_constant()

                #Add a module to catch possible major systematics that need to be masked out before continuuing:

                if (np.max(np.abs(additive_bkg.values)) > sys_threshold) or (len(np.where(tpf.quality > 0)[0]) > 0):   #None of the normally extracted objects has additive components with absolute values over 0.2 ish.

                    redo_with_mask = input('Additive trends in the background indicate major systematics; add a cadence mask (Y/N/(A)utomatic/(L)oad) ? ')

                    if redo_with_mask in ['L', 'l', 'Load', 'load']:
                        print('Loading cadence mask from file.')

                        mask_file = os.path.join(mask_dir, f"{target}_sector{sec}_mask.txt")
                        if os.path.exists(mask_file):
                            with open(mask_file, 'r') as f:
                                all_mask_limits = []
                                for line in f:
                                    line = line.strip()
                                    if not line:
                                        continue  # skip empty lines
                                    parts = line.split(',')
                                    if len(parts) == 2:
                                        try:
                                            start = int(parts[0])
                                            end = int(parts[1])

                                            cadence_mask = np.zeros(len(tpf.time), dtype=bool)
                                            # Clamp to valid range
                                            start = max(0, int(start))
                                            end = min(len(tpf.time) - 1, int(end))
                                            if start <= end:
                                                cadence_mask[start:end+1] = True
                                            tpf = tpf[~cadence_mask]
                                            
                                        except ValueError:
                                            continue  # skip malformed lines

                            additive_bkg = DesignMatrix(tpf.flux[:, allfaint_mask]).pca(additive_hybrid_pcas)
                            additive_bkg_and_constant = additive_bkg.append_constant()
            
                    if redo_with_mask in ['A', 'a', 'Automatic', 'automatic']:
                        print('Automatically adding cadence mask to the light curve.')

                        # mask exposures where the additive background is outside 100 std from the median
                        sorted_vals = np.sort(additive_bkg.values, axis=0)
                        n = sorted_vals.shape[0]
                        center = n // 2
                        half_window = 100  # 100 values total

                        # Handle edge cases if n < 100
                        if n < 100:
                            central = sorted_vals
                        else:
                            central = sorted_vals[center - half_window : center + half_window, :]

                        std_central = np.std(central, axis=0)

                        mask = np.abs(additive_bkg.values - np.median(additive_bkg.values, axis=0)) > 1e2 * std_central

                        # find the cadences where the mask is True
                        cadence_mask = np.any(mask, axis=1)

                        # applying the mask to the tpf object
                        tpf = tpf[~cadence_mask]
                        
                        # updating the additive_bkg variable
                        additive_bkg = DesignMatrix(tpf.flux[:, allfaint_mask]).pca(additive_hybrid_pcas)
                        additive_bkg_and_constant = additive_bkg.append_constant()


                    if redo_with_mask in ['Y', 'y', 'Yes', 'yes']:

                        number_masked_regions = 1 #set to 1 at first, for this mask.

                        all_mask_limits = []

                        fig_cm = plt.figure(dpi = 200, figsize=(12, 4))
                        ax_cm = fig_cm.add_subplot()

                        # plot additive background values vs time
                        ax_cm.plot(additive_bkg.values, label='Additive Background PCA Components', lw = 0.2)

                        # show on plot where the quality flags are
                        quality_flag_indices = np.where(tpf.quality > 0)[0]
                        for i, idx in enumerate(quality_flag_indices):
                            ax_cm.axvline(idx, color='red', linestyle='--', label='Quality Flag' if i == 0 else "", lw = 0.5, alpha = 0.5, zorder=0)

                        # limit x axis to +-100 from where the first and last quality flags are
                        if len(quality_flag_indices) > 0:
                            first_quality_flag = quality_flag_indices[0]
                            last_quality_flag = quality_flag_indices[-1]
                            ax_cm.set_xlim(max(-100, first_quality_flag - 100), min(len(tpf.time)+100, last_quality_flag + 100))

                            
                        ax_cm.legend()

                        plt.title('Select first and last cadence to define mask region:')
                        masked_cadence_limits = []
                        cid_cm = fig_cm.canvas.mpl_connect('button_press_event',onclick_cm)

                        all_mask_limits.append(masked_cadence_limits)

                        plt.show()
                        plt.close(fig_cm)

                        if len(masked_cadence_limits) != 0:

                            if masked_cadence_limits[0] >= 0:
                                first_timestamp = tpf.time[masked_cadence_limits[0]].value
                            else:
                                first_timestamp = 0
                            if masked_cadence_limits[1] < len(tpf.time) -1:
                                last_timestamp = tpf.time[masked_cadence_limits[1]].value
                            else:
                                last_timestamp = tpf.time[-1].value

                            cadence_mask = ~((tpf.time.value >= first_timestamp) & (tpf.time.value <= last_timestamp))

                            tpf = tpf[cadence_mask]

                            additive_bkg = DesignMatrix(tpf.flux[:, allfaint_mask]).pca(additive_hybrid_pcas)
                            additive_bkg_and_constant = additive_bkg.append_constant()

                            print(np.max(np.abs(additive_bkg.values)))


                        for i in range(0,max_masked_regions):

                            if np.max(np.abs(additive_bkg.values)) > sys_threshold  and number_masked_regions <= max_masked_regions:

                                number_masked_regions += 1

                                print('Systematics remain; define the next masked region.')
                                print(np.max(np.abs(additive_bkg.values)))
                                fig_cm = plt.figure(dpi = 200, figsize=(12, 4))
                                ax_cm = fig_cm.add_subplot()
                                ax_cm.plot(additive_bkg.values)

                                # show on plot where the quality flags are
                                quality_flag_indices = np.where(tpf.quality > 0)[0]
                                for i, idx in enumerate(quality_flag_indices):
                                    ax_cm.axvline(idx, color='red', linestyle='--', label='Quality Flag' if i == 0 else "", lw = 0.5, alpha = 0.5, zorder=0)

                                # limit x axis to +-100 from where the first and last quality flags are
                                if len(quality_flag_indices) > 0:
                                    first_quality_flag = quality_flag_indices[0]
                                    last_quality_flag = quality_flag_indices[-1]
                                    ax_cm.set_xlim(max(-1, first_quality_flag - 100), min(len(tpf.time), last_quality_flag + 100))

                                ax_cm.legend()

                                plt.title('Select first and last cadence to define mask region:')
                                masked_cadence_limits = []
                                cid_cm = fig_cm.canvas.mpl_connect('button_press_event',onclick_cm)

                                plt.show()
                                plt.close(fig_cm)

                                all_mask_limits.append(masked_cadence_limits)

                                if len(masked_cadence_limits) != 0:


                                    if masked_cadence_limits[0] >= 0:
                                        first_timestamp = tpf.time[masked_cadence_limits[0]].value
                                    else:
                                        first_timestamp = 0
                                    if masked_cadence_limits[1] < len(tpf.time) -1:
                                        last_timestamp = tpf.time[masked_cadence_limits[1]].value
                                    else:
                                        last_timestamp = tpf.time[-1].value


                                    cadence_mask = ~((tpf.time.value >= first_timestamp) & (tpf.time.value <= last_timestamp))

                                    tpf = tpf[cadence_mask]

                                    additive_bkg = DesignMatrix(tpf.flux[:, allfaint_mask]).pca(additive_hybrid_pcas)
                                    additive_bkg_and_constant = additive_bkg.append_constant()

                                else:
                                    number_masked_regions = max_masked_regions+1    #stops the loop if the user no longer wishes to add more regions.

                        # ask if cadence mask should be saved to a file
                        save_mask = input("Save the cadence mask to a .txt file for later use? (Y/N): ")
                        if save_mask.strip().upper() == 'Y':
                            # create the output directory if it doesn't exist
                            if not os.path.exists(mask_dir):
                                os.makedirs(mask_dir)

                            # save the cadence mask to a .txt file
                            mask_file = os.path.join(mask_dir, f"{target}_sector{sec}_mask.txt")
                            with open(mask_file, 'w') as f:
                                for limits in all_mask_limits:
                                    f.write(f"{limits[0]},{limits[1]}\n")
                            print(f"Cadence mask saved to {mask_file}")


                # Now we correct all the bright pixels EXCLUDING THE SOURCE by the background, so we can find the remaining multiplicative trend

                r = RegressionCorrector(LightCurve(time=tpf.time, flux=tpf.time.value*0))

                corrected_pixels = []
                for idx in range(allbright_mask.sum()):
                    r.lc.flux = tpf.flux[:, allbright_mask][:, idx].value
                    r.lc.flux_err = tpf.flux_err[:, allbright_mask][:, idx].value
                    r.correct(additive_bkg_and_constant, propagate_errors=prop_error_flag)
                    corrected_pixels.append(r.corrected_lc.flux)


                #Getting the multiplicative effects now from the bright pixels.

                multiplicative_hybrid_pcas = multiplicative_pca_num
                multiplicative_bkg = DesignMatrix(np.asarray(corrected_pixels).T).pca(multiplicative_hybrid_pcas)

                #Create a design matrix using only the multiplicative components determined from the additively-corrected bright sources for simple hybrid method:
                dm_mult = multiplicative_bkg
                dm_mult = dm_mult.append_constant()

                #Now get the raw light curve.
                lc = tpf.to_lightcurve(aperture_mask=aper_mod)
            #  lc = lc[lc.flux_err > 0]        #This was suggested by an error message to prevent the "flux uncertainties" problem.

                median_flux_precorr = np.median(lc.flux.value) #Calculate the median flux before the background subtraction upcoming.



                #Begin the SIMPLE HYBRID METHOD
                #First, simple background subtraction to handle additive effects:
                lc_bg = tpf.to_lightcurve(method='sap',corrector=None,aperture_mask = allfaint_mask)

                num_pixels_faint = np.count_nonzero(allfaint_mask)
                num_pixels_mask = np.count_nonzero(aper_mod)
                percent_of_bg_in_src = num_pixels_mask / num_pixels_faint

                lc_bg_time = lc_bg.time.value
                lc_bg_flux = lc_bg.flux.value
                lc_bg_fluxerr = lc_bg.flux_err.value

                lc_bg_scaled = lc_bg_flux - (1-percent_of_bg_in_src)*lc_bg_flux

                lc.flux = lc.flux.value - lc_bg_scaled

                #Replace any errors that are zero or negative with the mean error:

                mean_error = np.mean(lc.flux_err[np.isfinite(lc.flux_err)])
                lc.flux_err = np.where(lc.flux_err == 0,mean_error,lc.flux_err)
                lc.flux_err = np.where(lc.flux_err < 0,mean_error,lc.flux_err)
                lc.flux_err = lc.flux_err.value

                #And correct regressively for the multiplicative effects in the simple hybrid method:

                corrector_1 = RegressionCorrector(lc)
                clc = corrector_1.correct(dm_mult,propagate_errors=prop_error_flag)

                #Compute over- and under-fitting metrics using Lightkurve's lombscargle and neighbors methods:
                corrector_1.original_lc = corrector_1.lc
                sh_overfit_metric = corrector_1.compute_overfit_metric()
                #sh_underfit_metric = corrector_1.compute_underfit_metric() #Has to wait until we make our own using quaver-derived light curves of nearby targets.

                #The background subtraction can sometimes cause fluxes below the source's median
                #to be slightly negative; this enforces a minimum of zero, but can be ignored.

                if np.min(clc.flux.value) < 0:

                    dist_to_zero = np.abs(np.min(clc.flux.value))
                    clc.flux = clc.flux.value + dist_to_zero

                # Optional additive correction back to original median:
                median_flux_postsub = np.median(clc.flux.value)
                additive_rescale_factor = median_flux_precorr - median_flux_postsub
                #clc.flux = clc.flux.value + additive_rescale_factor    #uncomment if you want to use this.

                var_amplitude = np.max(clc.flux.value) - np.min(clc.flux.value)
                percent_variability = (var_amplitude / median_flux_precorr)*100



                #For the FULL HYBRID METHOD:
                #We make a fancy hybrid design matrix collection that has two orders of the additive effects, and the multiplicative effects.

                additive_bkg_squared = deepcopy(additive_bkg)
                additive_bkg_squared.df = additive_bkg_squared.df**2

                dmc = DesignMatrixCollection([additive_bkg_and_constant, additive_bkg_squared, multiplicative_bkg])
                lc_full = tpf.to_lightcurve(aperture_mask=aper_mod)

                r2 = RegressionCorrector(lc_full)

                r2.lc.flux = lc_full.flux.value
                r2.lc.flux_err = lc_full.flux_err.value
                clc_full = r2.correct(dmc,propagate_errors=prop_error_flag)

                #Compute over- and under-fitting metrics using Lightkurve's lombscargle and neighbors methods:
                r2.original_lc = r2.lc
                fh_overfit_metric = r2.compute_overfit_metric()
                #fh_underfit_metric = r2.compute_underfit_metric()  #Has to wait until we make our own using quaver-derived light curves of nearby targets.


                #clc_full = RegressionCorrector(lc_full).correct(dmc,propagate_errors=prop_error_flag)

                #Now we begin the SIMPLE PCA METHOD with components of all non-source pixels.

                raw_lc_OF = tpf.to_lightcurve(aperture_mask=aper_mod)

                #Replace any errors that are zero or negative with the mean error:
                raw_lc_OF.flux_err = np.where(raw_lc_OF.flux_err == 0,mean_error,raw_lc_OF.flux_err)
                raw_lc_OF.flux_err = np.where(raw_lc_OF.flux_err < 0,mean_error,raw_lc_OF.flux_err)
                raw_lc_OF.flux_err = np.where(np.isnan(raw_lc_OF.flux_err)==True,mean_error,raw_lc_OF.flux_err)

            #    raw_lc_OF = raw_lc_OF[raw_lc_OF.flux_err > 0]   #This was suggested by an error message to prevent the "flux uncertainties" problem.
                regressors_OF = tpf.flux[:,~aper_mod]

                number_of_pcas = pca_only_num

                dm_OF = DesignMatrix(regressors_OF,name='regressors')
                dm_pca_OF = dm_OF.pca(pca_only_num)
                dm_pca_OF = dm_pca_OF.append_constant()

                r3 = RegressionCorrector(raw_lc_OF)

                r3.lc.flux = raw_lc_OF.flux.value
                r3.lc.flux_err = raw_lc_OF.flux_err.value

                corrected_lc_pca_OF = r3.correct(dm_pca_OF,propagate_errors=prop_error_flag)

                #Compute over- and under-fitting metrics using Lightkurve's lombscargle and neighbors methods:
                r3.original_lc = r3.lc
                pca_overfit_metric = r3.compute_overfit_metric()
                #pca_underfit_metric = r3.compute_underfit_metric()  #Has to wait until we make our own using quaver-derived light curves of nearby targets.

                #AND PLOT THE CORRECTED LIGHT CURVE.

                fig2 = plt.figure(figsize=(12,8))
                gs = gridspec.GridSpec(ncols=3, nrows=3,wspace=0.5,hspace=0.5,width_ratios=[1,1,2])
                f_ax1 = fig2.add_subplot(gs[0, :])
                f_ax1.set_title(target+': Corrected Light Curve')
                f_ax2 = fig2.add_subplot(gs[1, :-1])
                if primary_correction_method == 1:
                    f_ax2.set_title('Principal Components')
                    f_ax4 = fig2.add_subplot(gs[1:,-1])
                elif primary_correction_method == 2 or primary_correction_method == 3:
                    f_ax2.set_title('Additive Components')
                    f_ax3 = fig2.add_subplot(gs[2:,:-1])
                    f_ax3.set_title('Multiplicative Components')
                    f_ax4 = fig2.add_subplot(gs[1:,-1])


                if primary_correction_method == 1:
                    corrected_lc_pca_OF.plot(ax=f_ax1)

                elif primary_correction_method == 2:
                    clc.plot(ax=f_ax1)

                elif primary_correction_method == 3:
                    clc_full.plot(ax=f_ax1)
                if primary_correction_method == 1:
                    f_ax2.plot(raw_lc_OF.time.value,dm_pca_OF.values[:,0:-1])

                elif primary_correction_method == 2 or primary_correction_method == 3:
                    f_ax2.plot(raw_lc_OF.time.value,additive_bkg.values)
                    f_ax3.plot(raw_lc_OF.time.value,multiplicative_bkg.values + np.arange(multiplicative_bkg.values.shape[1]) * 0.3)

                tpf.plot(ax=f_ax4,aperture_mask=aper_mod,title='Aperture')


###############################################################################
                    ## This section creates individual directories for each object in which the Quaver-processed light curve data is stored
                    ## and saves the output images and light curves to that directory. The output files WILL be overwritten with each Quaver run.
##############################################################################
                directory = str(target).replace(" ","")
                target_safename = target.replace(" ","")
                try:
                    os.makedirs(f'{out_dir}/'+target_safename)
                    print("Directory '% s' created\n" % directory)
                    if primary_correction_method == 1:
                        plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_SimplePCA_sector'+sec+'.pdf',format='pdf')
                        if show_plots: plt.show()
                    elif primary_correction_method == 2:
                        plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_SimpleHybrid_sector'+sec+'.pdf',format='pdf')
                        if show_plots: plt.show()
                    elif primary_correction_method == 3:
                        plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_FullHybrid_sector'+sec+'.pdf',format='pdf')
                        if show_plots: plt.show()

                except FileExistsError:
                    print("Saving to folder '% s'\n" % directory)
                    if primary_correction_method == 1:
                        plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_SimplePCA_sector'+sec+'.pdf',format='pdf')
                        if show_plots: plt.show()
                    elif primary_correction_method == 2:
                        plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_SimpleHybrid_sector'+sec+'.pdf',format='pdf')
                        if show_plots: plt.show()
                    elif primary_correction_method == 3:
                        plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_FullHybrid_sector'+sec+'.pdf',format='pdf')
                        if show_plots: plt.show()

                #Create saveable formats for the light curves and save to directory:
                pca_corrected_lc = np.column_stack((corrected_lc_pca_OF.time.value,corrected_lc_pca_OF.flux.value,corrected_lc_pca_OF.flux_err.value))
                simple_hybrid_corrected_lc = np.column_stack((clc.time.value,clc.flux.value,clc.flux_err.value))
                full_hybrid_corrected_lc = np.column_stack((clc_full.time.value,clc_full.flux.value,clc_full.flux_err.value))

                unstitched_lc_pca.append(pca_corrected_lc)
                unstitched_lc_simple_hyb.append(simple_hybrid_corrected_lc)
                unstitched_lc_full_hyb.append(full_hybrid_corrected_lc)

                np.savetxt(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_sector'+sec+'_PCA_lc.dat',pca_corrected_lc)
                np.savetxt(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_sector'+sec+'_simple_hybrid_lc.dat',simple_hybrid_corrected_lc)
                np.savetxt(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_sector'+sec+'_full_hybrid_lc.dat',full_hybrid_corrected_lc)\
                
                # ADDED: Save a plot with all three light curves for a sector as 4 subplots
                fig, axs = plt.subplots(3, 1, figsize=(6, 6), dpi=300, sharex=True)

                # Define a threshold for large gaps 
                gap_threshold = 0.5  # Adjust this value based on your data's time scale

                # Function to insert NaN for large gaps
                def insert_nan_for_gaps(time, flux, gap_threshold):
                    time_diff = np.diff(time)
                    large_gaps = np.where(time_diff > gap_threshold)[0]
                    time_with_nan = np.insert(time, large_gaps + 1, np.nan)
                    flux_with_nan = np.insert(flux, large_gaps + 1, np.nan)
                    return time_with_nan, flux_with_nan

                # Helper to plot scatter with error bars, handling NaNs for gaps
                def scatter_with_err(ax, time, flux, err, label, color):
                    mask = ~np.isnan(time)
                    ax.errorbar(time[mask], flux[mask], yerr=err[mask], fmt='.', ms=2, color='k', alpha=0.7, lw=0.3, zorder=0, label = label)
                    # plot running average with window size of 20
                    window_size = 20
                    running_avg = np.convolve(flux[mask], np.ones(window_size)/window_size, mode='valid')
                    t_full = time.copy()
                    avg_full = np.full_like(t_full, np.nan, dtype=float)
                    idx = np.where(~np.isnan(t_full))[0]
                    avg_full[idx[window_size//2 : -(window_size//2) + 1 if window_size % 2 == 0 else -(window_size//2)]] = running_avg
                    ax.plot(t_full, avg_full, color='r', lw=2, alpha=0.9)
                    
                # Uncorrected

                # Simple Hybrid
                time_simple_hybrid, flux_simple_hybrid = insert_nan_for_gaps(lc.time.value, clc.flux.value - np.mean(clc.flux.value), gap_threshold)
                _, err_simple_hybrid = insert_nan_for_gaps(lc.time.value, clc.flux_err.value, gap_threshold)
                scatter_with_err(axs[0], time_simple_hybrid, flux_simple_hybrid, err_simple_hybrid, 'Simple Hybrid', 'dodgerblue')
                axs[0].set_ylabel('Relative Flux')
                axs[0].set_title('Simple Hybrid')

                # Full Hybrid
                time_full_hybrid, flux_full_hybrid = insert_nan_for_gaps(lc.time.value, clc_full.flux.value - np.mean(clc_full.flux.value), gap_threshold)
                _, err_full_hybrid = insert_nan_for_gaps(lc.time.value, clc_full.flux_err.value, gap_threshold)
                scatter_with_err(axs[1], time_full_hybrid, flux_full_hybrid, err_full_hybrid, 'Full Hybrid', 'darkblue')
                axs[1].set_ylabel('Relative Flux')
                axs[1].set_title('Full Hybrid')

                # PCA
                time_pca, flux_pca = insert_nan_for_gaps(lc.time.value, corrected_lc_pca_OF.flux.value - np.mean(corrected_lc_pca_OF.flux.value), gap_threshold)
                _, err_pca = insert_nan_for_gaps(lc.time.value, corrected_lc_pca_OF.flux_err.value, gap_threshold)
                scatter_with_err(axs[2], time_pca, flux_pca, err_pca, 'PCA', 'darkorange')
                axs[2].set_ylabel('Relative Flux')
                axs[2].set_title('PCA')
                axs[2].set_xlabel('Time (BJD - 2457000)')

                for ax in axs:
                    ax.legend(loc='upper right', fontsize=8)
                    ax.set_xlim(lc.time.value[0] - 1, lc.time.value[-1] + 1)

                    # turn on grid
                    ax.grid(True, linestyle='--', alpha=0.3)

                plt.suptitle(f'{target} Sector {sec} Corrected Light Curves')
                plt.tight_layout(rect=[0, 0, 1, 0.97])
                plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_AllLightCurves_sector'+sec+'.png', format='png')
                if show_plots: plt.show()
                plt.close(fig)

                print("Sector, CCD, camera: ")
                print(sector_number,ccd,cam)
                print('\n')

                print("Over- and underfitting metrics: ")
                print('Full hybrid overfit: '+str(fh_overfit_metric))
                #print('Full hybrid underfit: '+str(fh_underfit_metric)+'\n')

                print('Simple hybrid overfit: '+str(sh_overfit_metric))
                #print('Simple hybrid underfit: '+str(sh_underfit_metric)+'\n')

                print('Simple PCA overfit: '+str(pca_overfit_metric))
                #print('Simple PCA underfit: '+str(pca_underfit_metric)+'\n')

                # ADDED: Save the overfitting metrics to a text file
                with open(f'{out_dir}/'+target_safename+'/'+target_safename+'_overfitting_metrics.txt', 'a') as f:

                    # write in selected pixels for aperture
                    f.write(f'Sector {sec}:\n')
                    f.write(f'CCD {ccd}, Camera {cam}\n')
                    f.write(f'Selected pixels: {row_col_coords}\n')

                    f.write(f'Sector {sec}:\n')
                    f.write(f'Full Hybrid Overfit: {fh_overfit_metric}\n')
                    f.write(f'Simple Hybrid Overfit: {sh_overfit_metric}\n')
                    f.write(f'Simple PCA  Overfit: {pca_overfit_metric}\n\n')

                    f.close()

                #Plot the resultant fit (matrices multiplied by the final coefficients) for each model with the original and final curves.
                sh_coeffs = corrector_1.coefficients
                fh_coeffs = r2.coefficients
                pca_coeffs = r3.coefficients

                #Compute Simple Hybrid total model:
                for i in range(0,multiplicative_hybrid_pcas):
                    if i ==0:
                        total_fit_sh = sh_coeffs[i]*corrector_1.dmc.matrices[0][i]
                    else:
                        total_fit_sh = total_fit_sh+sh_coeffs[i]*corrector_1.dmc.matrices[0][i]

                #Compute Full Hybrid total model:
                length_matrix_1 = np.shape(r2.dmc.matrices[0])[1]
                length_matrix_2 = np.shape(r2.dmc.matrices[1])[1]
                length_matrix_3 = np.shape(r2.dmc.matrices[2])[1]

                for j in range(0,3):
                    if j == 0:
                        for i in range(0,length_matrix_1-1):
                            if i == 0:
                                total_fit_fh = fh_coeffs[i]*r2.dmc.matrices[j][i]
                            else:
                                total_fit_fh = total_fit_fh + fh_coeffs[i]*r2.dmc.matrices[j][i]
                    elif j == 1:
                        for i in range(length_matrix_1,length_matrix_1+length_matrix_2):
                            for k in range(0,length_matrix_2):
                                total_fit_fh = total_fit_fh + fh_coeffs[i]*r2.dmc.matrices[j][k]

                    elif j == 2:
                        for i in range(length_matrix_1+length_matrix_2,len(fh_coeffs)):
                            for k in range(0,length_matrix_3):
                                total_fit_fh = total_fit_fh + fh_coeffs[i]*r2.dmc.matrices[j][k]

                #Compute PCA total model:
                for i in range(0,pca_only_num):
                    if i ==0:
                        total_fit_pca = pca_coeffs[i]*r3.dmc.matrices[0][i]
                    else:
                        total_fit_pca = total_fit_pca+pca_coeffs[i]*r3.dmc.matrices[0][i]


                #Plot the FH diagnostic:
                clc_full.plot(color='k',label='Corrected')
                plt.plot(lc.time.value,lc_full.flux.value,color='firebrick',label='Uncorrected')
                plt.plot(lc.time.value,total_fit_fh,color='cyan',label='Final Model')
                plt.legend()
                plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_FitDiagnostic_sector_'+sec+'full_hybrid.pdf',format='pdf')
                plt.close()

                #Plot the SH diagnostic:
                plot_scale_factor = np.median(lc_full.flux.value) - np.median(lc_bg_scaled)
                if plot_scale_factor > 0:
                    lc_bg_plot = lc_bg_scaled + plot_scale_factor
                else:
                    lc_bg_plot = lc_bg_scaled - plot_scale_factor

                _, axs = plt.subplots(2, figsize=(10, 6), sharex=True)
                ax = axs[0]
                lc_full.plot(ax=ax,color='firebrick',label='Uncorrected')
                ax.plot(lc.time.value,lc_bg_plot,color='magenta',label='Subtracted Background')
                ax.legend()
                ax = axs[1]
                clc.plot(ax=ax,color='k',label='Corrected')
                ax.plot(lc.time.value,total_fit_sh,color='cyan',label='Multiplicative Model')
                ax.legend()
                plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_FitDiagnostic_sector_'+sec+'simple_hybrid.pdf',format='pdf')
                plt.close()

                #Plot the PCA diagnostic:
                corrected_lc_pca_OF.plot(color='k',label='Corrected')
                plt.plot(lc.time.value,raw_lc_OF.flux.value,color='firebrick',label='Uncorrected')
                plt.plot(lc.time.value,total_fit_pca,color='cyan',label='Final Model')
                plt.legend()
                plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_FitDiagnostic_sector_'+sec+'_PCA.pdf',format='pdf')
                plt.close()

                print("\nMoving to next sector.\n")


    # If target coordinates are too close to edge on approach, this will skip that sector and read the next.
    # If target coordinates are too close to edge on exit, this will skip that sector.
    ## WARNING: May also occur if connection to HEASARC could not be made. Check website and/or internet connection.

    except (http.client.IncompleteRead):

        print("Unable to download FFI cutout. Desired target coordinates may be too near the edge of the FFI.\n")
        print("Could be inability to connect to HEASARC. Check website availability and/or internet connection.\n")

        if i != num_obs_sectors-1:

          print("\nMoving to next sector.\n")

        continue

#############################################
#############################################

#Stitch the sectors together:
print("No more observed sectors in this cycle.")

if len(unstitched_lc_simple_hyb)==0 and len(unstitched_lc_pca)==0 and len(unstitched_lc_full_hyb)==0:
    print("No light curve data extracted, exiting program.")

    sys.exit()

else:
    print('Stitching '+str(len(list_observed_sectors_in_cycle))+' sectors')

full_lc_time_pca,full_lc_flux_pca,full_lc_err_pca = lc_stitch(unstitched_lc_pca)
full_lc_time_sh,full_lc_flux_sh,full_lc_err_sh = lc_stitch(unstitched_lc_simple_hyb)
full_lc_time_fh,full_lc_flux_fh,full_lc_err_fh = lc_stitch(unstitched_lc_full_hyb)


#Remove single-cadence jumps greater than 1% of the flux on either side from all finished light curves
full_lc_time_pca,full_lc_flux_pca,full_lc_err_pca = remove_jumps(full_lc_time_pca,full_lc_flux_pca,full_lc_err_pca)
full_lc_time_sh,full_lc_flux_sh,full_lc_err_sh = remove_jumps(full_lc_time_sh,full_lc_flux_sh,full_lc_err_sh)
full_lc_time_fh,full_lc_flux_fh,full_lc_err_fh = remove_jumps(full_lc_time_fh,full_lc_flux_fh,full_lc_err_fh)

#Compile and save the corrected light curves.
pca_lc = np.column_stack((full_lc_time_pca,full_lc_flux_pca,full_lc_err_pca))
simple_hybrid_lc = np.column_stack((full_lc_time_sh,full_lc_flux_sh,full_lc_err_sh))
full_hybrid_lc = np.column_stack((full_lc_time_fh,full_lc_flux_fh,full_lc_err_fh))


np.savetxt(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_PCA_lc.dat',pca_lc)
np.savetxt(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_simple_hybrid_lc.dat',simple_hybrid_lc)
np.savetxt(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_full_hybrid_lc.dat',full_hybrid_lc)

#Plot the corrected light curves and save images.
fig_pca = plt.figure()
plt.errorbar(full_lc_time_pca,full_lc_flux_pca,yerr = full_lc_err_pca,marker='o',markersize=1,color='b',linestyle='none')
for i in range(0,len(unstitched_lc_pca)):
    last_time = unstitched_lc_pca[i][:,0][-1]
    plt.axvline(x=last_time,color='k',linestyle='--')
plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_stitched_lc_pca.pdf',format='pdf')
if primary_correction_method == 1 and show_plots:
        plt.show()
plt.close(fig_pca)

fig_sh = plt.figure()
plt.errorbar(full_lc_time_sh,full_lc_flux_sh,yerr = full_lc_err_sh,marker='o',markersize=1,color='b',linestyle='none')
for i in range(0,len(unstitched_lc_simple_hyb)):
    last_time = unstitched_lc_simple_hyb[i][:,0][-1]
    plt.axvline(x=last_time,color='k',linestyle='--')
plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_stitched_lc_simple_hybrid.pdf',format='pdf')
if primary_correction_method == 2 and show_plots:
    plt.show()
plt.close(fig_sh)

fig_fh = plt.figure()
plt.errorbar(full_lc_time_fh,full_lc_flux_fh,yerr = full_lc_err_fh,marker='o',markersize=1,color='b',linestyle='none')
for i in range(0,len(unstitched_lc_full_hyb)):
    last_time = unstitched_lc_full_hyb[i][:,0][-1]
    plt.axvline(x=last_time,color='k',linestyle='--')
plt.savefig(f'{out_dir}/'+target_safename+'/'+target_safename+'_cycle'+str(cycle)+'_stitched_lc_full_hybrid.pdf',format='pdf')
if primary_correction_method == 3 and show_plots:
    plt.show()
plt.close(fig_fh)

print ("Done!")
