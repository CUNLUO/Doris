import os
import numpy as np
from datetime import datetime
from collections import OrderedDict
from sentinel_1.functions.baselines import baselines
import copy
from copy import deepcopy
import shutil
from sentinel_1.main_code.resdata import ResData
from sentinel_1.functions.ESD_functions import get_f_DC_difference, get_offset, get_coordinates, freadbk
from scipy import linalg
import sentinel_1.main_code.resdata as resdata
from sentinel_1.main_code.dorisparameters import DorisParameters
import collections

from jobs import Jobs


class SingleMaster(object):

    def __init__(self,datastack=[],stack_read=False,start_date='',end_date='',master_date='',stack_folder='',processing_folder='',
                 input_files='',doris_path='', cpxfiddle_folder=''):
        # This function loads in a datastack to create a single master stack. Optional are the start date, end date and
        # master date. If they are not defined all dates will be loaded. The master date can be loaded later using the
        # master function. If you want a random master value, choose master_date='random'
        # Dates should be given as 'yyyy-mm-dd'. If stack_read is True information is read from the stack_folder. Otherwise
        # the datastack from an StackData object should be given as input.

        doris_parameters = DorisParameters(os.path.dirname(os.path.dirname(processing_folder))) # (assuming it is stored in the stackfolder
        self.doris_parameters = doris_parameters
        
        if not start_date:
            self.start_date = doris_parameters.start_date_default
        else:
            self.start_date = start_date
        if not end_date:
            self.end_date = doris_parameters.end_date_default
        else:
            self.end_date = end_date

        self.start_date = datetime.strptime(self.start_date,'%Y-%m-%d')
        self.end_date = datetime.strptime(self.end_date, '%Y-%m-%d')

        self.nr_of_jobs = doris_parameters.nr_of_jobs
        self.parallel = doris_parameters.parallel

        self.stack = dict()
        self.full_swath = dict()
        self.master_date = ''
        self.master_key = ''
        self.folder = processing_folder
        self.stack_folder = stack_folder

        self.doris_path = doris_parameters.doris_path
        self.cpxfiddle = doris_parameters.cpxfiddle_path  # '/...../cpxfiddle'
        self.function_path = doris_parameters.function_path
        self.input_files = input_files
        self.ESD_shift = dict()
        self.ESD_angle_pixel = dict()
        self.swath = dict()
        self.overlapping = []

        # Initialize ESD variables
        self.diff_matrix = []
        self.std_matrix = []
        self.to_angle_matrix = []
        self.weight_matrix = []
        self.weight = []

        if stack_folder:
            self.datastack = dict()
            self.stack_load()
            self.stack_read()

        if datastack:
            self.datastack = datastack
            self.stack_read()

        if master_date:
            master_date = datetime.strptime(master_date, '%Y-%m-%d')
            self.master(master_date)

        if processing_folder and not self.datastack:
            self.processing_read()

    def stack_load(self):
        # This function reads a datastack based on the data in a stack folder

        folders = next(os.walk(self.stack_folder))[1]
        folders = [fold for fold in folders if len(fold) == 8]

        for fold in folders:
            date = fold[:4] + '-' + fold[4:6] + '-' + fold[6:8]
            date_im = datetime.strptime(date,'%Y-%m-%d')

            if self.start_date <= date_im <= self.end_date:
                self.datastack[date] = dict()
                swaths = next(os.walk(os.path.join(self.stack_folder, fold)))[1]
                swaths = [fol for fol in swaths if len(fol) == 10]

                for swath in swaths:
                    self.datastack[date][swath] = dict()

                    bursts = next(os.walk(os.path.join(self.stack_folder, fold, swath)))[2]
                    bursts = [burst for burst in bursts if burst.endswith('.res')]

                    for burst in bursts:
                        slave_res = os.path.join(self.stack_folder, fold, swath, burst)
                        burst_name = 'burst_' + burst.split('.')[0][20:]
                        self.datastack[date][swath][burst_name] = ResData(slave_res)

    def stack_read(self):
        dates = self.datastack.keys()
        for date in dates:
            date_im = datetime.strptime(date,'%Y-%m-%d')

            if self.start_date <= date_im <= self.end_date:
                self.stack[date] = OrderedDict()

                for swath in self.datastack[date].keys():
                    for burst in self.datastack[date][swath].keys():
                        new_name = swath + '_' + burst

                        self.stack[date][new_name] = dict()
                        self.stack[date][new_name]['slave'] = self.datastack[date][swath][burst]
                self.full_swath[date] = dict()
        if self.folder:
            print 'Because a datastack is defined using stack_folder or the datastack variable the processing folder is not read'

    def processing_read(self):
        # This function reads a processing datastack based on the processing folder

        folders = next(os.walk(self.folder))[1]
        folders = [fold for fold in folders if len(fold) == 17]

        self.stack = dict()

        for fold in folders:
            m_date = fold[:4] + '-' + fold[4:6] + '-' + fold[6:8]
            s_date = fold[9:13] + '-' + fold[13:15] + '-' + fold[15:17]
            s_datetime = datetime.strptime(s_date,'%Y-%m-%d')

            if m_date == self.master_date and (self.start_date <= s_datetime <= self.end_date):
                self.stack[s_date] = dict()
                self.full_swath[s_date] = dict()

                swaths = next(os.walk(os.path.join(self.folder, fold)))[1]
                swaths = [fol for fol in swaths if len(fol) == 10]

                for swath in swaths:
                    bursts = next(os.walk(os.path.join(self.folder, fold, swath)))[1]

                    for burst in bursts:
                        burst_name = swath + '_' + burst
                        self.stack[s_date][burst_name] = dict()

        self.read_res()

    def remove_finished(self, step='filtphase'):
        # Checks which processing folders are already finished based on the final step.
        # possible steps are ('dem_assist', 'comp_coregpm', 'interfero', 'coherence', 'subtr_refphase', 'subtr_refdem',
        # 'filtphase', 'unwrap', 'geocoding'
        self.read_res()

        for date in self.stack.keys():
            if 'ifgs' in self.full_swath[date].keys():
                if self.full_swath[date]['ifgs'].process_control[step] == '1':
                    self.stack.pop(date)
                    self.full_swath.pop(date)

    def report(self):
        # This function reports on which steps are already performed based on metadata
        print 'In progress!'

    def master(self,master_date):
        # Load master date
        self.master_date = master_date.strftime('%Y-%m-%d')
        self.master_key = self.master_date[:4] + self.master_date[5:7] + self.master_date[8:10]

        if not master_date in self.stack.keys():
            print 'Master date is not part of the datastack. If you do not need to initialize anymore this is not a problem.'

    def baseline(self):
        # Create baseline plot of datastack. Usefull to select the right master
        baselines(self.stackfolder,self.start_date,self.end_date)

    def initialize(self,path='',cascade=False):
        # If path is not defined the stack will be initialized in the same folder as the datastack. This function does:
        # - copy .res and .raw files

        os.chdir(self.folder)

        for date in self.stack.keys():
            if date == self.master_date:
                continue

            date_folder = self.image_path(date)
            if not os.path.exists(date_folder):
                os.mkdir(date_folder)

            for burst in self.stack[date].keys():
                swath_folder = self.swath_path(date,burst)
                if not os.path.exists(swath_folder):
                    os.mkdir(swath_folder)

                burst_folder = self.burst_path(date,burst)
                if not os.path.exists(burst_folder):
                    os.mkdir(burst_folder)
                os.chdir(burst_folder)

                # Copy data files
                m_source = self.dat_file(burst,self.master_date,full_path=True,swath=True)
                m_dest = self.dat_file(burst,date='master')
                s_source = self.dat_file(burst,date,full_path=True,swath=True)
                s_dest = self.dat_file(burst,date='slave')
                if not os.path.exists(m_dest):
                    shutil.copy(m_source,m_dest)
                if not os.path.exists(s_dest):
                    shutil.copy(s_source,s_dest)

                # Write res files
                new_filename = os.path.join(burst_folder,'slave.res')
                if not os.path.exists(new_filename):
                    res = deepcopy(self.stack[date][burst]['slave'])
                    res.processes['crop']['Data_output_file'] = 'slave' + res.processes['crop']['Data_output_file'][8:]
                    res.write(new_filename=new_filename)

                new_filename = os.path.join(burst_folder,'master.res')
                if not os.path.exists(new_filename):
                    res = deepcopy(self.stack[self.master_date][burst]['slave'])
                    res.processes['crop']['Data_output_file'] = 'master' + res.processes['crop']['Data_output_file'][8:]
                    res.write(new_filename=new_filename)

        self.create_full_swath()

        del self.stack[self.master_date]

    def create_full_swath(self):
        # Create folders with full swath for individual interferogram.

        dates = self.stack.keys()

        # Change the res files.
        for date in dates:
            bursts = self.stack[date].keys()
            self.full_swath[date] = copy.deepcopy(self.stack[date][bursts[0]])


            # Information slave images
            az_time = self.full_swath[date]['slave'].processes['readfiles']['First_pixel_azimuth_time (UTC)']
            az_time = datetime.strptime(az_time,'%Y-%b-%d %H:%M:%S.%f')
            range_time = float(self.full_swath[date]['slave'].processes['readfiles']['Range_time_to_first_pixel (2way) (ms)'])

            # First adjust pixel and range times for pixel (1,1)
            for burst in bursts:
                az_burst = self.stack[date][burst]['slave'].processes['readfiles']['First_pixel_azimuth_time (UTC)']
                az_burst = datetime.strptime(az_burst,'%Y-%b-%d %H:%M:%S.%f')
                range_burst = float(self.stack[date][burst]['slave'].processes['readfiles']['Range_time_to_first_pixel (2way) (ms)'])

                if az_burst < az_time:
                    az_time = az_burst
                if range_burst < range_time:
                    range_time = range_burst

            az_time = az_time.strftime('%Y-%b-%d %H:%M:%S.%f')
            range_time = "{0:.15f}".format(range_time)
            self.full_swath[date]['slave'].processes['readfiles']['First_pixel_azimuth_time (UTC)'] = az_time
            self.full_swath[date]['slave'].processes['readfiles']['Range_time_to_first_pixel (2way) (ms)'] = range_time

            # Then change information on image size and crop.
            no_lines = self.full_swath[date]['slave'].processes['readfiles']['Number_of_lines_output_image']
            no_pixels = self.full_swath[date]['slave'].processes['readfiles']['Number_of_pixels_output_image']

            self.full_swath[date]['slave'].processes['readfiles']['Number_of_lines_original'] = no_lines
            self.full_swath[date]['slave'].processes['readfiles']['Number_of_pixels_original'] = no_pixels

            # Change readfiles
            self.full_swath[date]['slave'].processes['readfiles']['First_line (w.r.t. output_image)'] = '1'
            self.full_swath[date]['slave'].processes['readfiles']['Last_line (w.r.t. output_image)'] = no_lines
            self.full_swath[date]['slave'].processes['readfiles']['First_pixel (w.r.t. output_image)'] = '1'
            self.full_swath[date]['slave'].processes['readfiles']['Last_pixel (w.r.t. output_image)'] = no_pixels

            # Change in crop
            self.full_swath[date]['slave'].processes['crop']['First_line (w.r.t. original_image)'] = '1'
            self.full_swath[date]['slave'].processes['crop']['Last_line (w.r.t. original_image)'] = no_lines
            self.full_swath[date]['slave'].processes['crop']['First_pixel (w.r.t. original_image)'] = '1'
            self.full_swath[date]['slave'].processes['crop']['Last_pixel (w.r.t. original_image)'] = no_pixels
            self.full_swath[date]['slave'].processes['crop']['Data_output_file'] = date + '.raw'
            self.full_swath[date]['slave'].processes['crop'].pop('First_line (w.r.t. tiff_image)')
            self.full_swath[date]['slave'].processes['crop'].pop('Last_line (w.r.t. tiff_image)')

        # Write data to folder
        for date in dates:
            if date == self.master_date:
                continue

            folder = self.image_path(date)
            os.chdir(folder)

            # Create master.res
            master_path = self.image_path(date,'master.res')
            self.full_swath[self.master_date]['slave'].write(new_filename=master_path)

            # Create slave.res
            slave_path = self.image_path(date,'slave.res')
            self.full_swath[date]['slave'].write(new_filename=slave_path)

    def coarse_orbits(self):
        # Run coarse orbits for all bursts.
        ## aanpassen DLEVELT TODO
        job_list1 = []
        for date in self.stack.keys():
            job_list2 = []
            for burst in self.stack[date].keys():

                path = self.burst_path(date, burst)
                command2 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.coarseorb')
                job_list2.append([path, command2])
                if(not(self.parallel)):
                    os.chdir(path)
                    os.system(command2)
            if(self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list2)

            # Run coarse orbits for full swath
            folder = self.image_path(date)
            command1 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.coarseorb')
            job_list1.append([folder, command1])
            if(not(self.parallel)):
                os.chdir(folder)
                os.system(command1)
        if(self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(job_list1)

    def coarse_correlation(self,ps = False):
        # Run coarse correlation.
        for date in self.stack.keys():
            job_list1 = []
            job_list2 = []
            for burst in self.stack[date].keys():

                path = self.burst_path(date, burst)
                os.chdir(path)
                if ps is True:
                    master_file = self.dat_file(burst,date='master',full_path=False)
                    command1 = 'python -m ' + 'get_winpos' + ' ' + master_file + ' master.res 21 winpos_cc.asc'
                    job_list1.append([path, command1])
                    command2 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.coarsecorr')
                    job_list2.append([path, command2])
                    if(not(self.parallel)):
                        # TODO David
                        os.system('python -m ' + 'get_winpos' + ' ' + master_file + ' master.res 21 winpos_cc.asc')
                        os.system(command2)
                if ps is False:
                    command = self.doris_path + ' ' + os.path.join(self.input_files, 'input.coarsecorr')
                    job_list1.append([path, command])
                    if(not(self.parallel)):
                        os.system(command)

            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list1)
                jobs.run(job_list2)

    def correct_coarse_correlation(self):
        # Correct coarse orbits to same reference system for whole image.
        self.read_res()

        for date in self.stack.keys():

            bursts = self.stack[date].keys()
            real_trans_p = []
            real_trans_l = []
            crop_shift_p = []
            crop_shift_l = []

            for burst in bursts:

                s_first_pix = self.stack[date][burst]['slave'].processes['readfiles']['First_pixel (w.r.t. output_image)']
                s_first_line = self.stack[date][burst]['slave'].processes['readfiles']['First_line (w.r.t. output_image)']
                m_first_pix = self.stack[date][burst]['master'].processes['readfiles']['First_pixel (w.r.t. output_image)']
                m_first_line = self.stack[date][burst]['master'].processes['readfiles']['First_line (w.r.t. output_image)']

                s_first_pix_c = self.stack[date][burst]['slave'].processes['crop']['First_pixel (w.r.t. original_image)']
                s_first_line_c = self.stack[date][burst]['slave'].processes['crop']['First_line (w.r.t. original_image)']
                m_first_pix_c = self.stack[date][burst]['master'].processes['crop']['First_pixel (w.r.t. original_image)']
                m_first_line_c = self.stack[date][burst]['master'].processes['crop']['First_line (w.r.t. original_image)']

                coarse_p = self.stack[date][burst]['ifgs'].processes['coarse_correl']['Coarse_correlation_translation_pixels']
                coarse_l = self.stack[date][burst]['ifgs'].processes['coarse_correl']['Coarse_correlation_translation_lines']

                crop_p = int(s_first_pix) - int(m_first_pix) - int(s_first_pix_c) + int(m_first_pix_c)
                crop_l = int(s_first_line) - int(m_first_line) - int(s_first_line_c) + int(m_first_line_c)
                crop_shift_p.append(crop_p)
                crop_shift_l.append(crop_l)
                real_trans_p.append(int(coarse_p) + crop_p)
                real_trans_l.append(int(coarse_l) + crop_l)

            im_trans_p = int(round(np.median(real_trans_p)))
            im_trans_l = int(round(np.median(real_trans_l)))

            for burst, p_shift, l_shift in zip(bursts, crop_shift_p, crop_shift_l):

                trans_l = str(im_trans_l - l_shift)
                trans_p = str(im_trans_p - p_shift)
                self.stack[date][burst]['ifgs'].processes['coarse_correl']['Coarse_correlation_translation_pixels'] = trans_p
                self.stack[date][burst]['ifgs'].processes['coarse_correl']['Coarse_correlation_translation_lines'] = trans_l
                self.stack[date][burst]['ifgs'].processes['coarse_correl']['Initial_Offset_CoarseCorr_pixels'] = trans_p
                self.stack[date][burst]['ifgs'].processes['coarse_correl']['Initial_Offset_CoarseCorr_lines'] = trans_l
                self.stack[date][burst]['ifgs'].processes['coarse_correl']['Slope_CoarseCorr_pixels'] = '0'
                self.stack[date][burst]['ifgs'].processes['coarse_correl']['Slope_CoarseCorr_lines'] = '0'
            self.full_swath[date]['ifgs'].processes['coarse_orbits']['Coarse_orbits_translation_pixels'] = str(im_trans_p)
            self.full_swath[date]['ifgs'].processes['coarse_orbits']['Coarse_orbits_translation_lines'] = str(im_trans_l)

        self.update_res()

    def deramp(self, master=True):
        # Deramp slave and masters and slaves of bursts.

        job_list1 = []
        job_list2 = []
        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                path = self.burst_path(date, burst)

                master_file = self.dat_file(burst,date='master',full_path=False)
                slave_file = self.dat_file(burst,date='slave',full_path=False)

                if os.path.exists(os.path.join(path, master_file + '.orig')) or master == False:
                    command1 = ''
                else:
                    command1 = 'python ' + os.path.join(self.function_path, 'do_deramp_SLC.py') + ' ' + master_file + ' master.res'
                job_list1.append([path, command1])

                if os.path.exists(os.path.join(path, 'slave_rsmp.raw.orig')):
                    command2 = 'cp slave_rsmp.raw.orig slave_rsmp.raw'
                elif os.path.exists(os.path.join(path, slave_file + '.orig')):
                    command2 = ''
                else:
                    command2 = 'python ' + os.path.join(self.function_path, 'do_deramp_SLC.py') + ' ' + slave_file + ' slave.res'
                job_list2.append([path, command2])

                if(not(self.parallel)):
                    os.chdir(path)
                    os.system(command1)
                    os.system(command2)
        if(self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(job_list1)
            jobs.run(job_list2)

    def icc_burst(self, ps=False):
        # Do the icc per burst
        self.read_res()

        job_list1 = []
        job_list2 = []

        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                path = self.burst_path(date,burst)
                master_file = self.dat_file(burst,date='master',full_path=False)
                if(not(self.parallel)):
                    os.chdir(path)
                if ps == True:
                    command1 = 'python -m' + 'get_winpos'  + ' ' + master_file + ' master.res 101 winpos_fine.asc'
                    job_list1.append([path, command1])
                    command2 = self.doris_path + ' ' + os.path.join(self.input_files,'input.finecoreg_icc_pointscat')
                    job_list2.append([path, command2])
                    if (not(self.parallel)):
                        os.system(command1)
                        os.system(command2)
                elif ps == False:
                    command = self.doris_path + ' ' + os.path.join(self.input_files,'input.finecoreg')
                    job_list1.append([path,command])
                    if (not(self.parallel)):
                        os.system(command)
        if (self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(job_list1)
            jobs.run(job_list2)

    def coreg_full_swath(self):
        # Do the combined icc and dem coregistration for the full swath

        # First read all .res files again.
        self.read_res()

        for date in self.stack.keys():
            # We start by adding the windows of the first burst.
            no_offset = 0
            bursts = self.stack[date].keys()
            new_icc = copy.deepcopy(self.stack[date][bursts[0]]['ifgs'].processes['fine_coreg'])

            im_trans_p = self.full_swath[date]['ifgs'].processes['coarse_orbits']['Coarse_orbits_translation_pixels']
            im_trans_l = self.full_swath[date]['ifgs'].processes['coarse_orbits']['Coarse_orbits_translation_lines']

            for burst in bursts:

                icc = self.stack[date][burst]['ifgs'].processes['fine_coreg']
                position = self.stack[date][burst]['master'].processes['readfiles']

                trans_p = self.stack[date][burst]['ifgs'].processes['coarse_correl']['Coarse_correlation_translation_pixels']
                trans_l = self.stack[date][burst]['ifgs'].processes['coarse_correl']['Coarse_correlation_translation_lines']
                p_shift_offset = int(im_trans_p) - int(trans_p)
                l_shift_offset = int(im_trans_l) - int(trans_l)

                p_offset = int(position['First_pixel (w.r.t. output_image)']) - 1
                l_offset = int(position['First_line (w.r.t. output_image)']) - 1
                window_no = int(icc['Number_of_correlation_windows'])

                for row in range(1,window_no+1):
                    dat = copy.deepcopy(icc['row_' + str(row)])
                    dat[0] = str(no_offset)
                    dat[1] = str(int(dat[1]) + l_offset)
                    dat[2] = str(int(dat[2]) + p_offset)
                    dat[3] = str(float(dat[3]) + float(l_shift_offset))
                    dat[4] = str(float(dat[4]) + float(p_shift_offset))
                    new_icc['row_' + str(no_offset + 1)] = dat

                    no_offset += 1

            new_icc['Number_of_correlation_windows'] = str(no_offset)

            # Finally save to .res file
            self.full_swath[date]['ifgs'].insert(new_icc,'fine_coreg')
            # And write .res file
            res_path = self.image_path(date,file_path='ifgs.res')
            self.full_swath[date]['ifgs'].write(new_filename=res_path)

    def dac_bursts(self):
        # Do the DEM coregistration and coregpm for the full swath

        for date in self.stack.keys():
            job_list = []
            for burst in self.stack[date].keys():
                path = self.burst_path(date, burst)
                command = self.doris_path + ' ' + os.path.join(self.input_files,'input.dembased')
                job_list.append([path, command])
                if(not(self.parallel)):
                    os.chdir(path)
                    os.system(command)
            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list)

    def coreg_bursts(self,no_poly=True):
        # Write coregistration results from full swath to individual bursts

        self.read_res()

        for date in self.stack.keys():
            # First read the polynomials and normalization lines/pixels from full swath

            path = self.image_path(date)
            os.chdir(path)
            os.system(self.doris_path + ' ' + os.path.join(self.input_files,'input.coregpm'))

            self.read_res()

            coreg = copy.deepcopy(self.full_swath[date]['ifgs'].processes['comp_coregpm'])
            norm_line = [float(coreg['Normalization_Lines'].split()[0]),float(coreg['Normalization_Lines'].split()[1])]
            norm_pix = [float(coreg['Normalization_Pixels'].split()[0]),float(coreg['Normalization_Pixels'].split()[1])]
            degree = int(coreg['Degree_cpm'])

            La = 0; Lb = 0; Lc = 0; Ld = 0; Le = 0; Lf = 0
            Pa = 0; Pb = 0; Pc = 0; Pd = 0; Pe = 0; Pf = 0

            # Load the polynomial from the full swath
            if degree == 0 and no_poly == False:
                Lf = float(coreg['row_0'][0])
                Pf = float(coreg['row_1'][0])
            if degree == 1 and no_poly == False:
                Lf = float(coreg['row_0'][0])
                Le = float(coreg['row_1'][0])
                Ld = float(coreg['row_2'][0])
                Pf = float(coreg['row_3'][0])
                Pe = float(coreg['row_4'][0])
                Pd = float(coreg['row_5'][0])
            if degree == 2 and no_poly == False:
                Lf = float(coreg['row_0'][0])
                Le = float(coreg['row_1'][0])
                Ld = float(coreg['row_2'][0])
                Lc = float(coreg['row_4'][0])
                Lb = float(coreg['row_3'][0])
                La = float(coreg['row_5'][0])
                Pf = float(coreg['row_6'][0])
                Pe = float(coreg['row_7'][0])
                Pd = float(coreg['row_9'][0])
                Pc = float(coreg['row_8'][0])
                Pb = float(coreg['row_10'][0])
                Pa = float(coreg['row_11'][0])

            for burst in self.stack[date].keys():
                # Now convert to burst coreg using the pixel and line offset
                line_burst = int(self.stack[date][burst]['master'].processes['readfiles']['First_line (w.r.t. output_image)'])
                pixel_burst = int(self.stack[date][burst]['master'].processes['readfiles']['First_pixel (w.r.t. output_image)'])

                # And convert to an offset in the [-2,2] domain
                l0 = (line_burst-norm_line[0]) / (norm_line[1]-norm_line[0]) * 4
                p0 = (pixel_burst-norm_pix[0]) / (norm_pix[1]-norm_pix[0]) * 4

                # Finally convert variables. We assume a 2 degree polynomial.
                # y = ax'^2+bz'^2+cx'z'+(2ax0+cz0+d)x'+(2bz0+cx0+e)z'+(a0^2+bz0^2+cx0z0+dx0+ez0+f)
                p_poly = [0,0,0,0,0,0]
                p_poly[0] = Pa*p0**2 + Pb*l0**2 + Pc*p0*l0 + Pd*p0 + Pe*l0 + Pf
                p_poly[1] = 2*Pb*l0 + Pc*p0 + Pe
                p_poly[2] = 2*Pa*l0 + Pc*p0 + Pd
                p_poly[3] = Pc
                p_poly[4] = Pb
                p_poly[5] = Pa

                l_poly = [0,0,0,0,0,0]
                l_poly[0] = La*l0**2 + Lb*p0**2 + Lc*p0*l0 + Ld*l0 + Le*p0 + Lf
                l_poly[1] = 2*Lb*p0 + Lc*l0 + Le
                l_poly[2] = 2*La*p0 + Lc*l0 + Ld
                l_poly[3] = Lc
                l_poly[4] = Lb
                l_poly[5] = La

                # lambda function for pixel and line coordinates
                l_eq = lambda l,p: l_poly[5]*l**2 + l_poly[4]*p**2 + l_poly[3]*l*p + l_poly[2]*l + l_poly[1]*p + l_poly[0]
                p_eq = lambda l,p: p_poly[5]*p**2 + p_poly[4]*l**2 + p_poly[3]*l*p + p_poly[2]*p + p_poly[1]*l + p_poly[0]

                # Save new coregistration function to burst
                coreg['Degree_cpm'] = str(degree)
                if degree == 0:
                    coreg['row_0'] = ["{0:.8e}".format(l_poly[0]), '0', '0']
                    coreg['row_1'] = ["{0:.8e}".format(p_poly[0]), '0', '0']
                if degree == 1:
                    coreg['row_0'] = ["{0:.8e}".format(l_poly[0]), '0', '0']
                    coreg['row_1'] = ["{0:.8e}".format(l_poly[1]), '1', '0']
                    coreg['row_2'] = ["{0:.8e}".format(l_poly[2]), '0', '1']
                    coreg['row_3'] = ["{0:.8e}".format(p_poly[0]), '0', '0']
                    coreg['row_4'] = ["{0:.8e}".format(p_poly[1]), '1', '0']
                    coreg['row_5'] = ["{0:.8e}".format(p_poly[2]), '0', '1']
                if degree == 2:
                    coreg['row_0'] = ["{0:.8e}".format(l_poly[0]), '0', '0']
                    coreg['row_1'] = ["{0:.8e}".format(l_poly[1]), '1', '0']
                    coreg['row_2'] = ["{0:.8e}".format(l_poly[2]), '0', '1']
                    coreg['row_3'] = ["{0:.8e}".format(l_poly[4]), '2', '0']
                    coreg['row_4'] = ["{0:.8e}".format(l_poly[3]), '1', '1']
                    coreg['row_5'] = ["{0:.8e}".format(l_poly[5]), '0', '2']
                    coreg['row_6'] = ["{0:.8e}".format(p_poly[0]), '0', '0']
                    coreg['row_7'] = ["{0:.8e}".format(p_poly[1]), '1', '0']
                    coreg['row_8'] = ["{0:.8e}".format(p_poly[2]), '0', '1']
                    coreg['row_9'] = ["{0:.8e}".format(p_poly[4]), '2', '0']
                    coreg['row_10'] = ["{0:.8e}".format(p_poly[3]), '1', '1']
                    coreg['row_11'] = ["{0:.8e}".format(p_poly[5]), '0', '2']

                coreg['Deltaline_slave00_poly'] = "{0:.8e}".format(-l_eq(-2.0, -2.0))
                coreg['Deltapixel_slave00_poly'] = "{0:.8e}".format(-p_eq(-2.0, -2.0))
                coreg['Deltaline_slave0N_poly'] = "{0:.8e}".format(-l_eq(-2.0, 2.0))
                coreg['Deltapixel_slave0N_poly'] = "{0:.8e}".format(-p_eq(-2, 2.0))
                coreg['Deltaline_slaveN0_poly'] = "{0:.8e}".format(-l_eq(2.0, -2.0))
                coreg['Deltapixel_slaveN0_poly'] = "{0:.8e}".format(-p_eq(2.0, -2.0))
                coreg['Deltaline_slaveNN_poly'] = "{0:.8e}".format(-l_eq(2.0, 2.0))
                coreg['Deltapixel_slaveNN_poly'] = "{0:.8e}".format(-p_eq(2.0, 2.0))

                # Finally add the Normalization lines / pixels
                lines = (int(self.stack[date][burst]['master'].processes['crop']['Last_line (w.r.t. original_image)']) -
                         int(self.stack[date][burst]['master'].processes['crop']['First_line (w.r.t. original_image)']))
                pixels = (int(self.stack[date][burst]['master'].processes['crop']['Last_pixel (w.r.t. original_image)']) -
                         int(self.stack[date][burst]['master'].processes['crop']['First_pixel (w.r.t. original_image)']))

                # Save pixels lines
                coreg['Normalization_Lines'] = "{0:.8e}".format(1) + ' ' + "{0:.8e}".format(lines)
                coreg['Normalization_Pixels'] = "{0:.8e}".format(1) + ' ' + "{0:.8e}".format(pixels)

                # Copy coregistration from full swath to burst
                try:
                    self.stack[date][burst]['ifgs'].insert(coreg,'comp_coregpm')
                except:
                    self.stack[date][burst]['ifgs'].update(coreg,'comp_coregpm')

            self.update_res()

        # Save .res files.
        self.update_res()

    def fake_coregmp(self):
        # This function is used if only geometrical coregistatration is used.

        coreg = OrderedDict()
        self.read_res()

        coreg['Degree_cpm'] = '0'
        coreg['Normalization_Lines'] = ''
        coreg['Normalization_Pixels'] = ''
        coreg['Estimated_coefficientsL'] = ''
        coreg['row_0'] = ["{0:.8e}".format(0), '0', '0']
        coreg['Estimated_coefficientsP'] = ''
        coreg['row_1'] = ["{0:.8e}".format(0), '0', '0']

        coreg['Deltaline_slave00_poly'] = "{0:.8e}".format(0)
        coreg['Deltapixel_slave00_poly'] = "{0:.8e}".format(0)
        coreg['Deltaline_slave0N_poly'] = "{0:.8e}".format(0)
        coreg['Deltapixel_slave0N_poly'] = "{0:.8e}".format(0)
        coreg['Deltaline_slaveN0_poly'] = "{0:.8e}".format(0)
        coreg['Deltapixel_slaveN0_poly'] = "{0:.8e}".format(0)
        coreg['Deltaline_slaveNN_poly'] = "{0:.8e}".format(0)
        coreg['Deltapixel_slaveNN_poly'] = "{0:.8e}".format(0)

        for date in self.stack.keys():
            for burst in self.stack[date].keys():
                # Now convert to burst coreg using the pixel and line offset
                lines = (int(self.stack[date][burst]['master'].processes['crop']['Last_line (w.r.t. original_image)']) -
                         int(self.stack[date][burst]['master'].processes['crop']['First_line (w.r.t. original_image)']))
                pixels = (int(self.stack[date][burst]['master'].processes['crop']['Last_pixel (w.r.t. original_image)']) -
                         int(self.stack[date][burst]['master'].processes['crop']['First_pixel (w.r.t. original_image)']))

                # Save pixels lines
                coreg['Normalization_Lines'] = "{0:.8e}".format(1) + ' ' + "{0:.8e}".format(lines)
                coreg['Normalization_Pixels'] = "{0:.8e}".format(1) + ' ' + "{0:.8e}".format(pixels)

                # Copy coregistration from full swath to burst
                try:
                    self.stack[date][burst]['ifgs'].insert(coreg,'comp_coregpm')
                except:
                    self.stack[date][burst]['ifgs'].update(coreg,'comp_coregpm')

    def dac_full_swath(self):
        # This function reads the dem shift result files from the full swath and saves them to both data and result
        # files of individual bursts.

        self.read_res()

        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                master_dat = self.stack[date][burst]['master'].processes['crop']
                lines = int(master_dat['Last_line (w.r.t. original_image)']) - int(master_dat['First_line (w.r.t. original_image)'])
                pixels = int(master_dat['Last_pixel (w.r.t. original_image)']) - int(master_dat['First_pixel (w.r.t. original_image)'])
                ref_offset_p = int(self.full_swath[date]['ifgs'].processes['coarse_orbits']['Coarse_orbits_translation_pixels'])
                ref_offset_l = int(self.full_swath[date]['ifgs'].processes['coarse_orbits']['Coarse_orbits_translation_lines'])
                offset_p = int(self.stack[date][burst]['ifgs'].processes['coarse_correl']['Coarse_correlation_translation_pixels'])
                offset_l = int(self.stack[date][burst]['ifgs'].processes['coarse_correl']['Coarse_correlation_translation_lines'])

                file_path = self.burst_path(date, burst, file_path='dac_delta_pixel.raw')

                if not os.path.exists(file_path + '.new'):
                    d_pixel = np.memmap(file_path, dtype=np.dtype('float64'), shape=(lines+1,pixels+1))
                    n_pixel = np.memmap(file_path + '.new', mode='w+', dtype=np.dtype('float64'), shape=(lines+1,pixels+1))
                    n_pixel[:,:] = d_pixel[:,:] - (offset_p - ref_offset_p)
                    n_pixel.flush()

                if not os.path.exists(file_path + '.new'):
                    file_path = self.burst_path(date, burst, file_path='dac_delta_line.raw')
                    d_line = np.memmap(file_path, dtype=np.dtype('float64'), shape=(lines+1,pixels+1))
                    n_line = np.memmap(file_path + '.n', mode='w+', dtype=np.dtype('float64'), shape=(lines+1,pixels+1))
                    n_line[:,:] = d_line[:,:] - (offset_l - ref_offset_l)
                    n_line.flush()

        # Write delta line/pixel to burst folder
        self.concatenate('dac_delta_line.raw.new', 'dac_delta_line.raw.new',dt=np.dtype('float64'))
        self.concatenate('dac_delta_pixel.raw.new', 'dac_delta_pixel.raw.new',dt=np.dtype('float64'))


        for date in self.stack.keys():

            bursts = self.stack[date].keys()

            res_dem = deepcopy(self.stack[date][bursts[0]]['ifgs'].processes['dem_assist'])
            master_crop = deepcopy(self.full_swath[date]['master'].processes['crop'])

            # Update fields to dimensions of master burst.
            res_dem['First_line (w.r.t. original_master)'] = master_crop['First_line (w.r.t. original_image)']
            res_dem['Last_line (w.r.t. original_master)'] = master_crop['Last_line (w.r.t. original_image)']
            res_dem['First_pixel (w.r.t. original_master)'] = master_crop['First_pixel (w.r.t. original_image)']
            res_dem['Last_pixel (w.r.t. original_master)'] = master_crop['Last_pixel (w.r.t. original_image)']
            lines = int(master_crop['Last_line (w.r.t. original_image)']) - int(master_crop['First_line (w.r.t. original_image)'])
            res_dem['Number of lines'] = str(lines + 1)
            pixels = int(master_crop['Last_pixel (w.r.t. original_image)']) - int(master_crop['First_pixel (w.r.t. original_image)'])
            res_dem['Number of pixels'] = str(pixels + 1)

            # Load image data
            file_path = self.image_path(date, file_path='dac_delta_pixel.raw')
            command = 'mv ' + file_path + '.new ' + file_path
            os.system(command)
            d_pixel = np.memmap(file_path, dtype=np.dtype('float64'), shape=(lines+1,pixels+1))
            file_path = self.image_path(date, file_path='dac_delta_line.raw')
            command = 'mv ' + file_path + '.new ' + file_path
            os.system(command)
            d_line = np.memmap(file_path, dtype=np.dtype('float64'), shape=(lines+1,pixels+1))

            # Correct for corner information.
            res_dem['Number of pixels'] = str(pixels + 1)
            res_dem['Deltaline_slave00_dem'] = str(-d_line[0,0])
            res_dem['Deltapixel_slave00_dem'] = str(-d_pixel[0,0])
            res_dem['Deltaline_slave0N_dem'] = str(-d_line[0,-1])
            res_dem['Deltapixel_slave0N_dem'] = str(-d_pixel[0,-1])
            res_dem['Deltaline_slaveN0_dem'] = str(-d_line[-1,0])
            res_dem['Deltapixel_slaveN0_dem'] = str(-d_pixel[-1,0])
            res_dem['Deltaline_slaveNN_dem'] = str(-d_line[-1,-1])
            res_dem['Deltapixel_slaveNN_dem'] = str(-d_pixel[-1,-1])

            self.full_swath[date]['ifgs'].insert(res_dem,process='dem_assist')

        self.update_res()

    def resample(self, type=''):
        # Resample slave bursts
        jobList1 = []
        jobList2 = []

        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                path = self.burst_path(date, burst)

                command1 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.resample')

                if type != 'ESD' and not os.path.exists(os.path.join(path, 'slave_rsmp.raw.old')):
                    # In this way we are always sure we have the right resampled data for ESD calculations.
                    command2 = 'cp slave_rsmp.raw slave_rsmp.raw.old'
                else:
                    command2 = ''

                jobList1.append([path, command1])
                jobList2.append([path, command2])

                if(not(self.parallel)):
                    os.chdir(path)
                    # Resample
                    os.system(command1)
                    os.system(command2)

        if(self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)

            jobs.run(jobList1)
            jobs.run(jobList2)

    def reramp(self, type=''):
        # This function reramps the radar data. If master is True, we assume that there is still an original master file
        # Which means that it is not needed to reramp that one. If master is false, only the slave is reramped.
        jobList1 = []
        jobList2 = []
        jobList3 = []

        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                path = self.burst_path(date, burst)
                master_file = self.dat_file(burst, date='master', full_path=False)
                os.chdir(path)

                command1 = ''
                command2 = ''
                command3 = ''

                if type == 'ESD' and not os.path.exists(os.path.join(path, 'slave_rsmp.raw.orig')):
                    # If we are after ESD and reramp is not jet done
                    command1 = 'python ' + os.path.join(self.function_path, 'do_reramp_SLC.py') + ' slave_rsmp.raw slave.res'
                elif type == 'ESD' and os.path.exists(os.path.join(path, 'slave_rsmp.raw.orig')):
                    command1 = 'cp slave_rsmp.raw.orig slave_rsmp.raw'
                    #command2 = 'python ' + os.path.join(self.function_path,
                    #                                    'do_reramp_SLC.py') + ' slave_rsmp.raw slave.res'
                elif type != 'ESD' and not os.path.exists(os.path.join(path, 'slave_rsmp.raw.old.orig')):
                    # If we are before the ESD step and reramp is not jet done.
                    command1 = 'python ' + os.path.join(self.function_path, 'do_reramp_SLC.py') + ' slave_rsmp.raw.old slave.res'
                elif type != 'ESD' and os.path.exists(os.path.join(path, 'slave_rsmp.raw.old.orig')):
                    command1 = 'cp slave_rsmp.raw.old.orig slave_rsmp.raw.old'
                    #command2 = 'python ' + os.path.join(self.function_path,
                    #                                    'do_reramp_SLC.py') + ' slave_rsmp.raw.old slave.res'

                if os.path.exists(os.path.join(path, master_file + '.orig')):
                    # If this image was deramped before.
                    command2 = 'mv ' + os.path.basename(master_file) + ' master_deramped.raw'
                    command3 = 'mv ' + os.path.basename(master_file) + '.orig ' + os.path.basename(master_file)

                jobList1.append([path, command1])
                jobList2.append([path, command2])
                jobList3.append([path, command3])


                if(not(self.parallel)):
                    os.chdir(path)
                    # Save the original deramped slave
                    os.system(command1)
                    os.system(command2)
                    os.system(command3)

        if(self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(jobList1)
            jobs.run(jobList2)
            jobs.run(jobList3)

    def interferogram(self, concatenate=True, type=''):

        self.read_res()
        # Make an interferogram for the different bursts. (Not always necessary)
        jobList1 = []
        jobList2 = []
        jobList3 = []
        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                path = self.burst_path(date, burst)
                os.chdir(path)

                if type != 'ESD':
                    command1 = 'cp slave_rsmp.raw.old slave_rsmp.raw'
                    command2 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.interferogram')
                    command3 = 'cp cint.raw cint.raw.old'
                else:
                    command1 = ''
                    command2 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.interferogram')
                    command3 = ''

                jobList1.append([path, command1])
                jobList2.append([path, command2])
                jobList3.append([path, command3])

                if (not(self.parallel)):
                    os.chdir(path)
                    os.system(command1)
                    os.system(command2)
                    os.system(command3)

        if (self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(jobList1)
            jobs.run(jobList2)
            jobs.run(jobList3)

        self.read_res()

        if concatenate == True:
            if type == 'ESD':
                cint_name = 'cint.raw'
            elif type != 'ESD':
                cint_name = 'cint.raw.old'

            self.concatenate(cint_name, cint_name, dt=np.dtype('complex64'))

            for date in self.stack.keys():

                # Add res file information
                no_lines = self.full_swath[date]['master'].processes['readfiles']['Number_of_lines_original']
                no_pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']
                line_0 = self.full_swath[date]['master'].processes['readfiles']['First_line (w.r.t. output_image)']
                line_1 = self.full_swath[date]['master'].processes['readfiles']['Last_line (w.r.t. output_image)']
                pix_0 = self.full_swath[date]['master'].processes['readfiles']['First_pixel (w.r.t. output_image)']
                pix_1 = self.full_swath[date]['master'].processes['readfiles']['Last_pixel (w.r.t. output_image)']

                burst = self.stack[date].keys()[0]
                res = copy.deepcopy(self.stack[date][burst]['ifgs'].processes['interfero'])

                res['First_line (w.r.t. original_master)'] = line_0
                res['Last_line (w.r.t. original_master)'] = line_1
                res['First_pixel (w.r.t. original_master)'] = pix_0
                res['Last_pixel (w.r.t. original_master)'] = pix_1
                res['Number of lines (multilooked)'] = no_lines
                res['Number of pixels (multilooked)'] = no_pixels

                self.full_swath[date]['ifgs'].insert(res, 'interfero')

                path = self.image_path(date)
                os.chdir(path)
                # Finally show preview based on cpxfiddle

                pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']

                mag = ' -w ' + pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' ' + cint_name + ' > ' + cint_name + '_mag.ras'
                os.system(self.cpxfiddle + mag)
                mix = ' -w ' + pixels + ' -e 0.3 -s 1.2 -q mixed -o sunraster -b -c jet -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' ' + cint_name + ' > ' + cint_name + '_mix.ras'
                os.system(self.cpxfiddle + mix)
                pha = ' -w ' + pixels + ' -q phase -o sunraster -b -c jet -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' ' + cint_name + ' > ' + cint_name + '_pha.ras'
                os.system(self.cpxfiddle + pha)

        self.update_res()

    def overlapping(self):
        # This function calculates the overlapping areas between different bursts. This function will give a list of
        # overlapping areas between the different bursts.

        for date in self.stack.keys():
            # First make a list of all min max coordinates of all bursts.

            x0=[]; x1=[]; y0=[]; y1=[]
            bursts = self.stack[date].keys()
            for burst in bursts:
                y0.append(int(self.stack[date][burst][type].processes['readfiles']['First_line (w.r.t. output_image)']))
                y1.append(int(self.stack[date][burst][type].processes['readfiles']['Last_line (w.r.t. output_image)']))
                x0.append(int(self.stack[date][burst][type].processes['readfiles']['First_pixel (w.r.t. output_image)']))
                x1.append(int(self.stack[date][burst][type].processes['readfiles']['Last_pixel (w.r.t. output_image)']))

            for b1 in range(len(bursts)):
                print 'hello'

    # To be added to single master stack
    def esd_ps(self):

        jobList = []
        # First run all the ESD calculations in parallel
        for date in [self.stack.keys()[0]]:
            bursts = self.stack[date].keys()
            sort_id = [int(dat[6]) * 100 + int(dat[17:]) for dat in bursts]
            bursts = [x for (y, x) in sorted(zip(sort_id, bursts))]

            for burst, id in zip(bursts, range(len(bursts))):

                nBurst = int(burst[17:])
                next_burst = burst[:17] + str(nBurst + 1)
                if next_burst in bursts:
                    path = self.swath_path(date, burst)
                    overlap = burst + '_' + next_burst
                    command = 'python ' + os.path.join(self.function_path,
                                                       'ESD_ps.py') + ' ' + self.folder + ' ' + overlap + ' 1'
                    jobList.append([path, command])

                if not (self.parallel):
                    os.chdir(path)
                    os.system(command)
        if (self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(jobList)

        # Now load the different matrices again.
        # Find all the overlaps and corresponding results:
        esd_folder = os.path.join(self.stack_folder, 'esd')
        folders = os.listdir(esd_folder)

        self.diff_matrix = np.zeros(shape=(len(folders), len(self.stack.keys()) + 1, len(self.stack.keys()) + 1))
        self.std_matrix = np.zeros(shape=(len(folders), len(self.stack.keys()) + 1, len(self.stack.keys()) + 1))
        self.to_angle_matrix = np.zeros(shape=(len(folders), len(self.stack.keys()) + 1, len(self.stack.keys()) + 1))
        self.weight = np.zeros(len(folders))

        for folder, n in zip(folders, range(len(folders))):
            f = os.path.join(esd_folder, folder)
            diff_m = np.load(os.path.join(f, 'diff_matrix.npy'))
            std_m = np.load(os.path.join(f, 'std_matrix.npy'))
            to_angle_m = np.load(os.path.join(f, 'to_angle_matrix.npy'))
            w = np.load(os.path.join(f, 'weight.npy'))

            self.diff_matrix[n,:,:] = diff_m
            self.std_matrix[n,:,:] = std_m
            self.to_angle_matrix[n,:,:] = to_angle_m
            self.weight[n] = w

    def network_esd(self, max_t_baseline=500):
        # This function calculates the ESD values using a network approach

        dates = (self.stack.keys())
        dates.append(self.master_date)
        dates = sorted(dates)

        self.weight_matrix = np.zeros(shape=(len(dates), len(dates)))

        for date, n in zip(dates, range(len(dates))):
            for date_2, num in zip(dates[n + 1:], range(n + 1, len(dates))):
                time_diff = np.abs((datetime.strptime(date, '%Y-%m-%d') - datetime.strptime(date_2, '%Y-%m-%d')).days)
                if time_diff < max_t_baseline:
                    coherence_date = np.exp(-(float(time_diff) / 100))
                    self.weight_matrix[n, num] = coherence_date

        w_matrix = np.sum((self.std_matrix != 0) * self.weight[:, None, None], 0)
        diff_matrix = np.sum(self.diff_matrix * self.weight[:, None, None], 0)
        diff_matrix[w_matrix > 0] = diff_matrix[w_matrix > 0] / w_matrix[w_matrix > 0]

        angle_pixel = np.sum(self.to_angle_matrix * self.weight[:, None, None], 0)
        angle_pixel[w_matrix > 0] = angle_pixel[w_matrix > 0] / w_matrix[w_matrix > 0]

        # In case we want to use the standard deviations...
        self.std_matrix = (np.sum(self.std_matrix**2 * self.weight[:, None, None], 0) +
                          np.sum(self.diff_matrix**2 * self.weight[:, None, None], 0))
        #self.std_matrix[w_matrix > 0] = np.sqrt(self.std_matrix[w_matrix > 0] / w_matrix[w_matrix > 0] - diff_matrix[w_matrix > 0])
        #self.std_matrix[w_matrix > 0] = self.std_matrix[w_matrix > 0] / w_matrix[np.sqrt(w_matrix) > 0]
        self.diff_matrix = diff_matrix

        # Finally calculate the network

        # Find the connections in the difference matrix
        m_s = np.where(self.diff_matrix > 0)
        weight = self.weight_matrix[self.diff_matrix > 0]

        # Find the master date
        master_num = dates.index(self.master_date)

        # Create the A matrix
        A = np.zeros(shape=(len(m_s[0]), np.max([np.max(m_s[0]), np.max(m_s[1])]) + 1))
        A[range(len(m_s[0])), m_s[0]] = 1
        A[range(len(m_s[0])), m_s[1]] = -1
        A = np.hstack((A[:, :master_num - 1], A[:, master_num:]))

        # Create the weight matrix
        W = np.zeros((len(m_s[0]), len(m_s[0])))
        id = range(len(m_s[0]))

        W[id, id] = 1 / weight
        W = np.linalg.inv(W)

        esd_diff = np.dot(np.dot(np.dot(np.linalg.inv(np.dot(np.dot(A.T, W), A)), A.T), W), self.diff_matrix[self.diff_matrix > 0])
        esd_residue = np.dot(A, esd_diff) - self.diff_matrix[self.diff_matrix > 0]
        sigma = np.std(esd_residue)

        dates = sorted(self.stack.keys())
        for date, shift, angle in zip(dates, esd_diff, angle_pixel):
            self.ESD_shift[date] = shift
            self.ESD_angle_pixel[date] = angle

    def network_esd_coh(self, max_t_baseline):
        # Based on ps point esd is calculated using a network approach

        esd_folder = os.path.join(self.folder, 'esd')
        if not os.path.exists(esd_folder):
            os.mkdir(esd_folder)

        overlaps = [os.path.abspath(name) for name in os.listdir(esd_folder) if os.path.isdir(name)]

        for overlap, i in zip(overlaps, range(len(overlaps))):
            files = os.listdir(overlap)

            s = overlap.split('_')
            path = self.swath_path(self.datastack.keys()[0], s[0] + '_' + s[1] + '_' + s[2] + '_' + s[3] + '_' + s[4])
            os.chdir(path)

            line_start, line_length, first_pixel_this, first_pixel_next, pixel_length, this_nr_oflines, \
            this_nr_ofpixels, next_nr_oflines, next_nr_ofpixels, PRF = get_coordinates(nBurst)

            if not dates:
                dates = [f[:-2] for f in files if f.endswith('_1')].sort()
            if not diff_matrix:
                diff_matrix = np.zeros(shape=(len(dates), len(dates), len(overlaps)))
            if not weight_matrix:
                weight_matrix = np.zeros(shape=(len(dates), len(dates), len(overlaps)))

            Nra = 10
            Naz = 2
            new_ra = pixel_length / Nra
            new_az = line_length / Naz

            df_dc = np.memmap(os.path.join(overlap, 'dc_df'), 'complex64', mode='r+', shape=(line_length, pixel_length))
            df_dc = df_dc[:new_az * 2, :new_ra * 10].reshape([new_az, Naz, new_ra, Nra]).mean(3).mean(1)

            for date, n in zip(dates, range(len(dates))):
                first_master = np.memmap(os.path.join(overlap, date + '_1'), 'complex64', mode='r+',
                                         shape=(line_length, pixel_length))
                second_master = np.memmap(os.path.join(overlap, date + '_2'), 'complex64', mode='r+',
                                          shape=(line_length, pixel_length))

                for date_2, num in zip(dates[n+1:], range(n+1, len(dates))):

                    if time_diff < max_t_baseline:
                        first_slave = np.memmap(os.path.join(overlap, date_2 + '_1'), 'complex64', mode='r+',
                                                shape=(line_length, pixel_length))
                        second_slave = np.memmap(os.path.join(overlap, date_2 + '_2'), 'complex64', mode='r+',
                                                 shape=(line_length, pixel_length))

                        ifgs_1 = (first_master * first_slave.conj())[:new_az * 2, :new_ra * 10].reshape([new_az, Naz, new_ra, Nra])
                        ifgs_2 = (second_master * second_slave.conj())[:new_az * 2, :new_ra * 10].reshape([new_az, Naz, new_ra, Nra])

                        coh = np.abs(ifgs_1 * ifgs_2.conj().sum(3).sum(1)) / \
                              np.sqrt((ifgs_1.real**2 + ifgs_1.imag**2).sum(3).sum(1) *
                                      (ifgs_2.real**2 + ifgs_2.imag**2).sum(3).sum(1))
                        double_diff = ifgs_1.mean(3).mean(1) * ifgs_2.mean(3).mean(1).conj()

                        weights = 2 * coh*coh / (1 - coh*coh)
                        weight = np.sum(weights[weights > 0.4])
                        pixel_diff = np.angle(np.sum(double_diff[weights > 0.4] * weights[weights > 0.4])) / \
                                     (PRF/(2*np.pi*np.sum(df_dc[weights > 0.4] * weights[weights > 0.4]) / weight))

                        diff_matrix[i, n, num] = pixel_diff
                        weight_matrix[i, n, num] = weight

        diff_matrix = diff_matrix.sum(0)
        weight_matrix = weight_matrix.sum(0)

        return diff_matrix, weight_matrix, dates

    def ESD(self):
        # Do ESD for individual bursts, combine results for all bursts and combine with coregistration parameters.

        for date in self.stack.keys():
            self.pi_shift[date] = dict()
            z = 0

            bursts = self.stack[date].keys()
            sort_id = [int(dat[6])*100 + int(dat[17:]) for dat in bursts]
            bursts = [x for (y,x) in sorted(zip(sort_id, bursts))]

            weight = [0] * len(bursts)
            shift = [0] * len(bursts)
            angle_v = [0] * len(bursts)

            for burst, id in zip(bursts, range(len(bursts))):

                nBurst = int(burst[17:])
                next_burst = burst[:17] + str(nBurst + 1)

                if next_burst in bursts:

                    path = self.swath_path(date,burst)
                    os.chdir(path)

                    str_burst = str(nBurst)
                    Df_DC = get_f_DC_difference(nBurst)
                    offset, W, angle = get_offset(nBurst, Df_DC)

                    print 'burst ' + str_burst + ' from ' + date + ' with weight ' + str(W) + ' and offset ' + str(offset)

                    shift[id] = offset
                    weight[id] = W
                    angle_v[id] = angle
                else:
                    z = 0

            # Assume constant offset
            x = np.sum(np.array(shift) * np.array(weight)) / np.sum(weight)
            angle = np.sum(np.array(angle_v) * np.array(weight)) / np.sum(weight)

            print 'total shift ' + date + ' is ' + str(x)
            self.ESD_shift[date] = x
            # This is not exact, but will suffice...
            self.ESD_angle_pixel[date] = angle

    def ESD_correct(self):

        self.read_res()
        jobList1 = []
        jobList2 = []

        for date in self.stack.keys():
            # First save the original data. This allows us to do the correction more than once on the same dataset...
            # Reruns of the scripts cause therefore no problems.

            path = self.image_path(date)
            dac_delta_line_dir_old = self.image_path(date, file_path='dac_delta_line_old.raw')

            command2 = ''
            if os.path.exists(dac_delta_line_dir_old):
                # ESD_correct is run before first copy the old to the current file.
                command1 = 'rm ' + os.path.join(path, 'dac_delta_line.raw')
                command2 = 'cp ' + os.path.join(path, 'dac_delta_line_old.raw') + ' ' + os.path.join(path, 'dac_delta_line.raw')
            else:
                # first time for ESD. First copy to old.
                command1 = 'cp ' + os.path.join(path, 'dac_delta_line.raw') + ' ' + os.path.join(path, 'dac_delta_line_old.raw')

            jobList1.append([path, command1])
            jobList2.append([path, command2])

            if (not (self.parallel)):
                os.chdir(path)
                os.system(command1)
                os.system(command2)

        if (self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(jobList1)
            jobs.run(jobList2)

        for date in self.stack.keys():
            ESD_pixels = self.ESD_shift[date]

            dac_delta_line_dir = self.image_path(date,file_path='dac_delta_line.raw')

            dt = np.dtype('float64')
            lines = int(self.full_swath[date]['master'].processes['readfiles']['Number_of_lines_original'])
            pixels = int(self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original'])

            data_dac = np.memmap(dac_delta_line_dir, dtype=dt, mode='r+', shape=(lines, pixels))

            # Write and save data
            # for diff, i in zip(ESD_pixels, range(len(ESD_pixels))):
            data_dac[:,:] = data_dac[:,:] + ESD_pixels
            data_dac.flush()

        for date in self.stack.keys():
            for burst in self.stack[date].keys():
                # Run this code for different super images. This function reads the dac delta file and corrects based on
                # on interpolated values for one row in the data.

                line_0 = int(self.stack[date][burst]['master'].processes['readfiles']['First_line (w.r.t. output_image)'])
                line_1 = int(self.stack[date][burst]['master'].processes['readfiles']['Last_line (w.r.t. output_image)'])
                pix_0 = int(self.stack[date][burst]['master'].processes['readfiles']['First_pixel (w.r.t. output_image)'])
                pix_1 = int(self.stack[date][burst]['master'].processes['readfiles']['Last_pixel (w.r.t. output_image)'])
                pixels = pix_1 - pix_0 + 1
                lines = line_1 - line_0 + 1

                dac_delta_line_dir = self.burst_path(date, burst, file_path='dac_delta_line.raw')
                dt = np.dtype('float64')
                data_dac = np.memmap(dac_delta_line_dir, dtype=dt, mode='r+', shape=(lines, pixels))

                # Write and save data
                ESD_diff = self.ESD_shift[date]
                data_dac[:,:] = data_dac[:,:] + ESD_diff
                data_dac.flush()

                # Correct for corner information.
                self.stack[date][burst]['ifgs'].processes['dem_assist']['Number of pixels'] = str(pixels + 1)
                self.stack[date][burst]['ifgs'].processes['dem_assist']['Deltaline_slave00_dem'] = str(-data_dac[0,0])
                self.stack[date][burst]['ifgs'].processes['dem_assist']['Deltaline_slave0N_dem'] = str(-data_dac[0,-1])
                self.stack[date][burst]['ifgs'].processes['dem_assist']['Deltaline_slaveN0_dem'] = str(-data_dac[-1,0])
                self.stack[date][burst]['ifgs'].processes['dem_assist']['Deltaline_slaveNN_dem'] = str(-data_dac[-1,-1])

        self.update_res()

    def ESD_correct_ramp(self, filename='cint.raw'):
        # This function correct for ESD using the expected ramp in the resampled slave image

        self.read_res()
        jobList = []

        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                path = self.burst_path(date, burst)

                offset = self.ESD_shift[date]
                angle = self.ESD_angle_pixel[date]
                angle_pixel = str(offset / angle)
                script = os.path.join(self.function_path, 'deramp_ESD.py')
                command = 'python ' + script + ' ' + path + ' ' + filename + ' ' + angle_pixel

                jobList.append([path, command])

                if not (self.parallel):
                    os.chdir(path)
                    os.system(command)

        if self.parallel:
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(jobList)

    def combine_slave(self):
        # This function concatenates the different slave values. Both ramped and deramped.

        self.read_res()
        self.concatenate('slave_rsmp.raw', 'slave_rsmp.raw', dt= np.dtype('complex64'))
        self.concatenate('slave_rsmp.raw.orig', 'slave_rsmp_deramped.raw', dt=np.dtype('complex64'))

        # Add the resample step to the .res file
        for date in self.stack.keys():
            burst = self.stack[date].keys()[0]
            slave_res = copy.deepcopy(self.stack[date][burst]['slave'].processes['resample'])

            # Read number of lines
            lines = int(self.full_swath[date]['master'].processes['readfiles']['Number_of_lines_original'])
            pixels = int(self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original'])

            # Add information to interfero step about lines and pixels.
            slave_res['First_line (w.r.t. original_master)'] = str(1)
            slave_res['Last_line (w.r.t. original_master)'] = str(lines)
            slave_res['First_pixel (w.r.t. original_master)'] = str(1)
            slave_res['Last_pixel (w.r.t. original_master)'] = str(pixels)
            slave_res['Data_output_file'] = 'slave_rsmp_deramped.raw'

            # Finally add to result file
            self.full_swath[date]['slave'].insert(slave_res,'resample')

            pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']

            mag = ' -w ' + pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 20/5 -f cr4 -l1 ' \
                                     '-p1 -P' + pixels + ' slave_rsmp.raw > slave_rsmp.ras'
            os.system(self.cpxfiddle + mag)
            mag = ' -w ' + pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 20/5 -f cr4 -l1 ' \
                         '-p1 -P' + pixels + ' slave_rsmp_deramped.raw > slave_rsmp_deramped.ras'
            os.system(self.cpxfiddle + mag)

        self.update_res()

    def combine_master(self):
        # This function concatenates the master files to one image.

        self.read_res()
        self.concatenate('master.raw', 'master.raw', dt= np.dtype('int32'))
        self.concatenate('master_deramped.raw', 'master_deramped.raw', dt= np.dtype('int32'))

        # Add the resample step to the .res file
        for date in self.stack.keys():
            # Write new name to master.res
            self.full_swath[date]['master'].processes['crop']['Data_output_file'] = 'master.raw'

            pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']

            mag = ' -w ' + pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 20/5 -f ci2 -l1 ' \
                                     '-p1 -P' + pixels + ' master.raw > master.ras'
            os.system(self.cpxfiddle + mag)
            mag = ' -w ' + pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 20/5 -f ci2 -l1 ' \
                         '-p1 -P' + pixels + ' master_deramped.raw > master_deramped.ras'
            os.system(self.cpxfiddle + mag)

        self.update_res()

    def compref_phase(self,concatenate=True):
        # This function performs the final steps in making an interferogram for all full images.

        for date in self.stack.keys():
            job_list1 = []
            for burst in self.stack[date].keys():
                path = self.burst_path(date, burst)
                command1 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.comprefpha')
                job_list1.append([path, command1])
                if (not(self.parallel)):
                    os.chdir(path)
                    os.system(command1)
            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list1)

    def ref_phase(self,concatenate=True):
        # This function performs the final steps in making an interferogram for all full images.

        for date in self.stack.keys():
            job_list2 = []
            for burst in self.stack[date].keys():
                path = self.burst_path(date, burst)
                command2 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.subtrrefpha')
                job_list2.append([path, command2])
                if (not(self.parallel)):
                    os.chdir(path)
                    os.system(command2)
            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list2)

        self.read_res()

        if concatenate == True:
            self.concatenate('cint_srp.raw', 'cint_srp.raw', dt= np.dtype('complex64'))
            for date in self.stack.keys():

                # Add res file information
                no_lines = self.full_swath[date]['master'].processes['readfiles']['Number_of_lines_original']
                no_pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']
                line_0 = self.full_swath[date]['master'].processes['readfiles']['First_line (w.r.t. output_image)']
                line_1 = self.full_swath[date]['master'].processes['readfiles']['Last_line (w.r.t. output_image)']
                pix_0 = self.full_swath[date]['master'].processes['readfiles']['First_pixel (w.r.t. output_image)']
                pix_1 = self.full_swath[date]['master'].processes['readfiles']['Last_pixel (w.r.t. output_image)']

                burst = self.stack[date].keys()[0]
                res_2 = copy.deepcopy(self.stack[date][burst]['ifgs'].processes['comp_refphase'])
                res_1 = copy.deepcopy(self.stack[date][burst]['ifgs'].processes['subtr_refphase'])

                res_2['First_line (w.r.t. original_master)'] = line_0
                res_2['Last_line (w.r.t. original_master)'] = line_1
                res_2['First_pixel (w.r.t. original_master)'] = pix_0
                res_2['Last_pixel (w.r.t. original_master)'] = pix_1
                res_2['Number of lines (multilooked)'] = no_lines
                res_2['Number of pixels (multilooked)'] = no_pixels

                self.full_swath[date]['ifgs'].insert(res_1, 'comp_refphase')
                self.full_swath[date]['ifgs'].insert(res_2, 'subtr_refphase')

                path = self.image_path(date)
                os.chdir(path)
                # Finally show preview based on cpxfiddle

                pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']

                mag = ' -w ' + pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint_srp.raw > interferogram_srp_mag.ras'
                os.system(self.cpxfiddle + mag)
                mix = ' -w ' + pixels + ' -e 0.3 -s 1.2 -q mixed -o sunraster -b -c jet -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint_srp.raw > interferogram_srp_mix.ras'
                os.system(self.cpxfiddle + mix)
                pha = ' -w ' + pixels + ' -q phase -o sunraster -b -c jet -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint_srp.raw > interferogram_srp_pha.ras'
                os.system(self.cpxfiddle + pha)

        self.update_res()

    def ref_dem(self,concatenate=True):
        # This function performs the final steps in making an interferogram for all full images.
        for date in self.stack.keys():
            job_list2 = []
            for burst in self.stack[date].keys():
                path = self.burst_path(date, burst)
                command2 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.subtrrefdem')
                job_list2.append([path, command2])
                if (not(self.parallel)):
                    os.chdir(path)
                    os.system(command2)
            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list2)

        self.read_res()

        if concatenate == True:
            self.concatenate('cint_srd.raw', 'cint_srd.raw', dt= np.dtype('complex64'))
            for date in self.stack.keys():

                # Add res file information
                no_lines = self.full_swath[date]['master'].processes['readfiles']['Number_of_lines_original']
                no_pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']
                line_0 = self.full_swath[date]['master'].processes['readfiles']['First_line (w.r.t. output_image)']
                line_1 = self.full_swath[date]['master'].processes['readfiles']['Last_line (w.r.t. output_image)']
                pix_0 = self.full_swath[date]['master'].processes['readfiles']['First_pixel (w.r.t. output_image)']
                pix_1 = self.full_swath[date]['master'].processes['readfiles']['Last_pixel (w.r.t. output_image)']

                burst = self.stack[date].keys()[0]
                res_2 = copy.deepcopy(self.stack[date][burst]['ifgs'].processes['comp_refdem'])
                res_1 = copy.deepcopy(self.stack[date][burst]['ifgs'].processes['subtr_refdem'])

                res_1['First_line (w.r.t. original_master)'] = line_0
                res_1['Last_line (w.r.t. original_master)'] = line_1
                res_1['First_pixel (w.r.t. original_master)'] = pix_0
                res_1['Last_pixel (w.r.t. original_master)'] = pix_1
                res_1['Number of lines (multilooked)'] = no_lines
                res_1['Number of pixels (multilooked)'] = no_pixels

                res_2['First_line (w.r.t. original_master)'] = line_0
                res_2['Last_line (w.r.t. original_master)'] = line_1
                res_2['First_pixel (w.r.t. original_master)'] = pix_0
                res_2['Last_pixel (w.r.t. original_master)'] = pix_1
                res_2['Number of lines (multilooked)'] = no_lines
                res_2['Number of pixels (multilooked)'] = no_pixels

                self.full_swath[date]['ifgs'].insert(res_1, 'comp_refdem')
                self.full_swath[date]['ifgs'].insert(res_2, 'subtr_refdem')

                path = self.image_path(date)
                os.chdir(path)
                # Finally show preview based on cpxfiddle

                pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']

                mag = ' -w ' + pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint_srd.raw > interferogram_srd_mag.ras'
                os.system(self.cpxfiddle + mag)
                mix = ' -w ' + pixels + ' -e 0.3 -s 1.2 -q mixed -o sunraster -b -c jet -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint_srd.raw > interferogram_srd_mix.ras'
                os.system(self.cpxfiddle + mix)
                pha = ' -w ' + pixels + ' -q phase -o sunraster -b -c jet -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint_srd.raw > interferogram_srd_pha.ras'
                os.system(self.cpxfiddle + pha)

        self.update_res()

    def compref_dem(self,concatenate=True):
        # This function performs the final steps in making an interferogram for all full images.
        for date in self.stack.keys():
            job_list1 = []
            for burst in self.stack[date].keys():
                path = self.burst_path(date, burst)
                command1 = self.doris_path + ' ' + os.path.join(self.input_files, 'input.comprefdem')
                job_list1.append([path, command1])
                if (not(self.parallel)):
                    os.chdir(path)
                    os.system(command1)
            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list1)

    def coherence(self,concatenate=True):
        # This function performs the final steps in making an interferogram for all full images.
        for date in self.stack.keys():
            job_list = []
            for burst in self.stack[date].keys():
                path = self.burst_path(date, burst)
                command = self.doris_path + ' ' + os.path.join(self.input_files, 'input.coherence')
                job_list.append([path, command])
                if (not(self.parallel)):
                    os.chdir(path)
                    os.system(command)
            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list)

        self.read_res()

        if concatenate == True:
            self.concatenate('coherence.raw', 'coherence.raw', dt=np.dtype('float32'))
            for date in self.stack.keys():

                # Add res file information
                no_lines = self.full_swath[date]['master'].processes['readfiles']['Number_of_lines_original']
                no_pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']
                line_0 = self.full_swath[date]['master'].processes['readfiles']['First_line (w.r.t. output_image)']
                line_1 = self.full_swath[date]['master'].processes['readfiles']['Last_line (w.r.t. output_image)']
                pix_0 = self.full_swath[date]['master'].processes['readfiles']['First_pixel (w.r.t. output_image)']
                pix_1 = self.full_swath[date]['master'].processes['readfiles']['Last_pixel (w.r.t. output_image)']

                burst = self.stack[date].keys()[0]
                res = copy.deepcopy(self.stack[date][burst]['ifgs'].processes['coherence'])

                res['First_line (w.r.t. original_master)'] = line_0
                res['Last_line (w.r.t. original_master)'] = line_1
                res['First_pixel (w.r.t. original_master)'] = pix_0
                res['Last_pixel (w.r.t. original_master)'] = pix_1
                res['Number of lines (multilooked)'] = no_lines
                res['Number of pixels (multilooked)'] = no_pixels

                self.full_swath[date]['ifgs'].insert(res, 'coherence')

                path = self.image_path(date)
                os.chdir(path)
                # Finally show preview based on cpxfiddle

                pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']

                mag = ' -w ' + pixels + ' -q normal -o sunraster -b -c gray -M 20/5 -r 0.0/1.0 -f r4 -l1 ' \
                                                 '-p1 -P' + pixels + ' coherence.raw > coherence.ras'
                os.system(self.cpxfiddle + mag)

        self.update_res()

    def phasefilt(self,concatenate=True):
        # This function performs the phase filtering of the individual bursts.

        for date in self.stack.keys():
            job_list = []
            for burst in self.stack[date].keys():
                path = self.burst_path(date, burst)
                command = self.doris_path + ' ' + os.path.join(self.input_files, 'input.phasefilt')
                job_list.append([path, command])
                if (not(self.parallel)):
                    os.chdir(path)
                    os.system(command)
            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list)

        self.read_res()

        if concatenate == True:

            self.concatenate('cint.0.2filtered', 'cint_filt.raw', dt=np.dtype('complex64'))
            for date in self.stack.keys():

                # Add res file information
                no_lines = self.full_swath[date]['master'].processes['readfiles']['Number_of_lines_original']
                no_pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']
                line_0 = self.full_swath[date]['master'].processes['readfiles']['First_line (w.r.t. output_image)']
                line_1 = self.full_swath[date]['master'].processes['readfiles']['Last_line (w.r.t. output_image)']
                pix_0 = self.full_swath[date]['master'].processes['readfiles']['First_pixel (w.r.t. output_image)']
                pix_1 = self.full_swath[date]['master'].processes['readfiles']['Last_pixel (w.r.t. output_image)']

                burst = self.stack[date].keys()[0]
                res = copy.deepcopy(self.stack[date][burst]['ifgs'].processes['filtphase'])

                res['First_line (w.r.t. original_master)'] = line_0
                res['Last_line (w.r.t. original_master)'] = line_1
                res['First_pixel (w.r.t. original_master)'] = pix_0
                res['Last_pixel (w.r.t. original_master)'] = pix_1
                res['Number of lines (multilooked)'] = no_lines
                res['Number of pixels (multilooked)'] = no_pixels

                self.full_swath[date]['ifgs'].insert(res, 'filtphase')

                path = self.image_path(date)
                os.chdir(path)
                # Finally show preview based on cpxfiddle

                pixels = self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original']

                mag = ' -w ' + pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint.0.2filtered > interferogram_filt_mag.ras'
                os.system(self.cpxfiddle + mag)
                mix = ' -w ' + pixels + ' -e 0.3 -s 1.2 -q mixed -o sunraster -b -c jet -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint.0.2filtered > interferogram_filt_mix.ras'
                os.system(self.cpxfiddle + mix)
                pha = ' -w ' + pixels + ' -q phase -o sunraster -b -c jet -M 20/5 -f cr4 -l1 ' \
                                                 '-p1 -P' + pixels + ' cint.0.2filtered > interferogram_filt_pha.ras'
                os.system(self.cpxfiddle + pha)

        self.update_res()

    def unwrap(self):
        # This function is used to call the unwrapping program snaphu via doris.

        for date in self.stack.keys():
            path = self.image_path(date)
            os.chdir(path)

            # First create an phase input file for unwrapping
            pixels = self.full_swath[date]['ifgs'].processes['filtphase']['Number of pixels (multilooked)']
            print pixels
            pha = ' -w ' + pixels + ' -q phase -o float -M 1/1 -f cr4 -l1 ' \
                                    '-p1 -P' + pixels + ' cint_filt_ml.raw > unwrap_input.raw'
            os.system(self.cpxfiddle + pha)

            command = self.doris_path + ' ' + os.path.join(self.input_files, 'input.unwrap')
            os.system(command)

            # And create an image using cpxfiddle

            pha = ' -w ' + pixels + ' -q normal -o sunraster -b -c jet -M 1/1 -f r4 -l1 ' \
                                    '-p1 -P' + pixels + ' unwrapped.raw > unwrapped.ras'
            os.system(self.cpxfiddle + pha)

    def concatenate(self, burst_file, master_file, dt=np.dtype(np.float32)):
        # Concatenate all burst to a single full swath product. If burst_file = 'master' then the input master files are read...

        self.read_res()
        job_list1 = []

        for date in self.stack.keys():
            path = self.image_path(date)

            command1 = 'python ' + os.path.join(self.function_path, 'concatenate_decatenate.py') + ' ' + path + ' concatenate ' + burst_file + ' ' + dt.name
            job_list1.append([path, command1])
            if not(self.parallel):
                os.chdir(path)
                os.system(command1)
        if (self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(job_list1)

            # Create master path
            # master = self.image_path(date,file_path=master_file)
            #
            # # Read image size
            # bursts = self.stack[date].keys()
            # no_lines = int(self.full_swath[date][type].processes['readfiles']['Number_of_lines_original'])
            # no_pixels = int(self.full_swath[date][type].processes['readfiles']['Number_of_pixels_original'])
            #
            # # First use memmap to get a memory map of the full file.
            # full_image = np.memmap(master, dtype=dt, mode='w+', shape=(no_lines, no_pixels))
            #
            # for burst in bursts:
            #     # Finally write all data from individual bursts to master file. We assume a simple 20 pixel offset from
            #     # the side to prevent copying data without information.
            #
            #     if burst_file == 'master':
            #         master_path = self.burst_path(date,burst)
            #         master_file = self.dat_file(burst,date='master',full_path=False)
            #         burst_dat = os.path.join(master_path, master_file)
            #     elif burst_file == 'master_deramped':
            #         master_path = self.burst_path(date,burst)
            #         master_file = self.dat_file(burst,date='master',full_path=False) + '.orig'
            #         burst_dat = os.path.join(master_path, master_file)
            #
            #     else:
            #         burst_dat = self.burst_path(date,burst,burst_file)
            #
            #     line_0 = int(self.stack[date][burst][type].processes['readfiles']['First_line (w.r.t. output_image)'])
            #     line_1 = int(self.stack[date][burst][type].processes['readfiles']['Last_line (w.r.t. output_image)'])
            #     pix_0 = int(self.stack[date][burst][type].processes['readfiles']['First_pixel (w.r.t. output_image)'])
            #     pix_1 = int(self.stack[date][burst][type].processes['readfiles']['Last_pixel (w.r.t. output_image)'])
            #
            #     burst_pix = pix_1 - pix_0 + 1
            #     burst_line = line_1 - line_0 + 1
            #
            #     # Cut out data with border of 20 px and write to file.
            #     burst_image = np.memmap(burst_dat, dtype=dt, mode='r', shape=(burst_line,burst_pix))
            #     full_image[(line_0+19):(line_1-20),(pix_0+19):(pix_1-20)] = burst_image[20:-20,20:-20]

        self.update_res()

    def multilook(self, ra=40, az=10,step='filtphase'):
        # This function does the multilooking using cpxfiddle and updates the resolution of the step variable. You
        # have to careful that if you want to perform this step to follow on with a smaller data file, for e.g. unwrapping
        # this should be the last mentioned step.

        if step == 'filtphase':
            filename = 'cint.0.2filtered'
            filename2 = 'cint_filt_ml.raw'
            type = 'cr4'
        elif step == 'coherence':
            filename = 'coherence.raw'
            filename2 = 'coherence_ml.raw'
            type = 'r4'
        elif step == 'subtr_refdem':
            filename = 'cint_srd.raw'
            filename2 = 'cint_srd_ml.raw'
            type = 'cr4'
        elif step == 'subtr_refpha':
            filename = 'cint_srp.raw'
            filename2 = 'cint_srp_ml.raw'
            type = 'cr4'
        elif step == 'interfero':
            filename = 'cint.raw'
            filename2 = 'cint_ml.raw'
            type = 'cr4'
        else:
            print('Choose for step between filtphase, coherence, subtrefdem, subtrefpha and interfero')

        self.read_res()

        for date in self.stack.keys():
            lines = int(self.full_swath[date]['master'].processes['readfiles']['Number_of_lines_original'])
            pixels = int(self.full_swath[date]['master'].processes['readfiles']['Number_of_pixels_original'])

            date_path = self.image_path(date)
            os.chdir(date_path)

            # Create cpxfiddle command
            command = ' -w ' + str(pixels) + ' -o float -M ' + str(ra) + '/'+ str(az) + ' -f ' + type + ' ' \
                                             '-l1 -p1 -P' + str(pixels) + ' -q normal ' + filename + ' > ' + filename2
            os.system(self.cpxfiddle + command)

            # Update res file
            new_lines = str(int(np.floor(lines / az)))
            new_pixels = str(int(np.floor(pixels / ra)))

            res = self.full_swath[date]['ifgs'].processes[step]

            res['Data_output_file'] = filename2
            res['Multilookfactor_azimuth_direction'] = str(az)
            res['Multilookfactor_range_direction'] = str(ra)
            res['Number of lines (multilooked)'] = new_lines
            res['Number of pixels (multilooked)'] = new_pixels

            self.full_swath[date]['ifgs'].processes[step] = res

            # Finally create an image using cpxfiddle (full resolution)
            if type == 'r4':
                # Only show the magnitude
                mag = ' -w ' + new_pixels + ' -e 0.3 -s 1.0 -q normal -o sunraster -b -c gray -M 1/1 -f r4 -l1 ' \
                                                 '-p1 -P' + new_pixels + ' ' + filename2 + ' > ' + filename2[:-4] + '.ras'
                os.system(self.cpxfiddle + mag)
            elif type == 'cr4':
                # Show the 3 images
                mag = ' -w ' + new_pixels + ' -e 0.3 -s 1.0 -q mag -o sunraster -b -c gray -M 1/1 -f cr4 -l1 ' \
                                                 '-p1 -P' + new_pixels + ' ' + filename2 + ' > ' + filename2[:-4] + '_mag.ras'
                os.system(self.cpxfiddle + mag)
                mix = ' -w ' + new_pixels + ' -e 0.3 -s 1.2 -q mixed -o sunraster -b -c jet -M 1/1 -f cr4 -l1 ' \
                                                 '-p1 -P' + new_pixels + ' ' + filename2 + ' > ' + filename[:-4] + '_mix.ras'
                os.system(self.cpxfiddle + mix)
                pha = ' -w ' + new_pixels + ' -q phase -o sunraster -b -c jet -M 1/1 -f cr4 -l1 ' \
                                                 '-p1 -P' + new_pixels + ' ' + filename2 + ' > ' + filename2[:-4] + '_pha.ras'
                os.system(self.cpxfiddle + pha)

        self.update_res()

    def decatenate(self,burst_file,master_file,dt=np.dtype(np.float32),type='master'):
        # Split full swath into different burst products. (to be used for DEM result splitting)

        self.read_res()
        job_list1 = []

        for date in self.stack.keys():
            path = self.image_path(date)

            command1 = os.path.join(self.function_path, 'concatenate_decatenate.py') + ' ' + path + ' decatenate ' + burst_file + ' ' + dt.str
            job_list1.append([path, command1])
            if not(self.parallel):
                os.chdir(path)
                os.system(command1)
        if (self.parallel):
            jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
            jobs.run(job_list1)

        #
        # for date in self.stack.keys():
        #     # Create master path
        #     master = self.image_path(date,file_path=master_file)
        #
        #     # Read image size
        #     bursts = self.stack[date].keys()
        #     no_lines = int(self.stack[date][bursts[0]][type].processes['readfiles']['Number_of_lines_output_image'])
        #     no_pixels = int(self.stack[date][bursts[0]][type].processes['readfiles']['Number_of_pixels_output_image'])
        #
        #     # First use memmap to get a memory map of the full file.
        #     full_image = np.memmap(master, dtype=dt, mode='r', shape=(no_lines, no_pixels))
        #
        #     for burst in bursts:
        #         # Finally write all data from individual bursts to master file. We assume a simple 20 pixel offset from
        #         # the side to prevent copying data without information.
        #
        #         burst_dat = self.burst_path(date,burst,burst_file)
        #
        #         line_0 = int(self.stack[date][burst][type].processes['readfiles']['First_line (w.r.t. output_image)'])
        #         line_1 = int(self.stack[date][burst][type].processes['readfiles']['Last_line (w.r.t. output_image)'])
        #         pix_0 = int(self.stack[date][burst][type].processes['readfiles']['First_pixel (w.r.t. output_image)'])
        #         pix_1 = int(self.stack[date][burst][type].processes['readfiles']['Last_pixel (w.r.t. output_image)'])
        #
        #         burst_pix = pix_1 - pix_0 + 1
        #         burst_line = line_1 - line_0 + 1
        #
        #         # Cut out data with border of 20 px and write to file.
        #         burst_image = np.memmap(burst_dat, dtype=dt, mode='w+', shape=(burst_line,burst_pix))
        #         burst_image[:,:] = full_image[line_0-1:line_1,pix_0-1:pix_1]
        #         burst_image.flush()
        #
        self.update_res()

    # Following functions are helper function which help acces the correct folders and files more easily:

    def burst_path(self,date,key,file_path=''):
        #TODO refactor string lengt code
        date_folder = self.master_date[:4] + self.master_date[5:7] + self.master_date[8:10] + '_' + date[:4] + date[5:7] + date[8:10]
        swath_burst = key.split('_')
        if file_path:
            file_path = os.path.join(self.folder, date_folder, swath_burst[0] + '_' + swath_burst[1]
                                     , swath_burst[3] + '_' + swath_burst[4]
                                     , file_path)
        else:
            file_path = os.path.join(self.folder, date_folder, swath_burst[0] + '_' + swath_burst[1]
                                     , swath_burst[3] + '_' + swath_burst[4]
                                     )

        return file_path

    def swath_path(self,date,key):
        #TODO refactor string lengt code
        date_folder = self.master_date[:4] + self.master_date[5:7] + self.master_date[8:10] + '_' + date[:4] + date[5:7] + date[8:10]
        swath_burst = key.split('_')
        file_path = os.path.join(self.folder, date_folder, swath_burst[0] + '_' + swath_burst[1])

        return file_path

    def image_path(self,date,file_path=''):
        #TODO refactor string lengt code
        date_folder = self.master_date[:4] + self.master_date[5:7] + self.master_date[8:10] + '_' + date[:4] + date[5:7] + date[8:10]
        if file_path:
            file_path = os.path.join(self.folder, date_folder, file_path)
        else:
            file_path = os.path.join(self.folder, date_folder)

        return file_path

    def dat_file(self,key,date,full_path=False,swath=False):
        #TODO refactor string lengt code
        # This function converts combinations of dates and keys to a datafile name

        string = '_iw_' + key[6] + '_burst_' + key[17:]

        if date == 'master' or date == 'slave':
            string = date + string + '.raw'
        else: # In case it is a date
            string = date[:4] + date[5:7] + date[8:10] + string + '.raw'

        if full_path is True:
            date_folder = date[:4] + date[5:7] + date[8:10]
            swath_folder = key[:10]
            burst_folder = key[11:]
            if swath is False:
                string = os.path.join(self.stack_folder,date_folder,swath_folder,burst_folder,string)
            elif swath is True:
                string = os.path.join(self.stack_folder,date_folder,swath_folder,string)

        return string

    def update_res(self):
        # Save to .res file based on the burst objects.

        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                files = self.stack[date][burst].keys()
                if 'slave' in files:
                    slave_res = self.burst_path(date,burst,'slave.res')
                    self.stack[date][burst]['slave'].write(new_filename=slave_res)
                if 'master' in files:
                    master_res = self.burst_path(date,burst,'master.res')
                    self.stack[date][burst]['master'].write(new_filename=master_res)
                if 'ifgs' in files:
                    ifgs_res = self.burst_path(date,burst,'ifgs.res')
                    self.stack[date][burst]['ifgs'].write(new_filename=ifgs_res)

            files = self.full_swath[date].keys()
            if 'slave' in files:
                slave_res = self.image_path(date,'slave.res')
                self.full_swath[date]['slave'].write(new_filename=slave_res)
            if 'master' in files:
                master_res = self.image_path(date,'master.res')
                self.full_swath[date]['master'].write(new_filename=master_res)
            if 'ifgs' in files:
                ifgs_res = self.image_path(date,'ifgs.res')
                self.full_swath[date]['ifgs'].write(new_filename=ifgs_res)

    def read_res(self):
        # Read .res data to the burst objects. Generally done after a processing step.

        for date in self.stack.keys():
            for burst in self.stack[date].keys():

                slave_res = self.burst_path(date,burst,'slave.res')
                master_res = self.burst_path(date,burst,'master.res')
                ifgs_res = self.burst_path(date,burst,'ifgs.res')

                if os.path.exists(slave_res):
                    self.stack[date][burst]['slave'] = ResData(filename=slave_res)
                if os.path.exists(master_res):
                    self.stack[date][burst]['master'] = ResData(filename=master_res)
                if os.path.exists(ifgs_res):
                    self.stack[date][burst]['ifgs'] = ResData(filename=ifgs_res)


            slave_res = self.image_path(date,'slave.res')
            master_res = self.image_path(date,'master.res')
            ifgs_res = self.image_path(date,'ifgs.res')

            if os.path.exists(slave_res):
                self.full_swath[date]['slave'] = ResData(filename=slave_res)
            if os.path.exists(master_res):
                self.full_swath[date]['master'] = ResData(filename=master_res)
            if os.path.exists(ifgs_res):
                self.full_swath[date]['ifgs'] = ResData(filename=ifgs_res)

    def del_res(self,type='ifgs',images=False,bursts=True):

        for date in self.stack.keys():
            for burst in self.stack[date].keys():
                if bursts == True:
                    res = self.burst_path(date,burst, type + '.res')
                    if os.path.exists(res):
                        os.remove(res)

            if images == True:
                res = self.image_path(date, type + '.res')
                if os.path.exists(res):
                    os.remove(res)

    def del_process(self,process,type='ifgs',images=False,bursts=True):
        # Delete a process from the .res files.

        self.read_res() # Read data

        for date in self.stack.keys():
            for burst in self.stack[date].keys():
                if bursts == True:
                    self.stack[date][burst][type].delete(process)
            if images == True:
                self.full_swath[date][type].delete(process)
        self.update_res()

    def calc_coordinates(self, createdem=False):
    # Calculate the coordinates of grid cells

        dates = self.stack.keys()

        for date in dates:
            job_list1 = []
            job_list2 = []

            doris_dir = self.doris_path

            for burst in self.stack[date].keys():
                path = self.burst_path(date, burst)

                # Create grid coordinates and heights
                if createdem == True:
                    dem_inputfile = os.path.join(self.input_files, 'input.createdem')
                else:
                    dem_inputfile = False

                # Run if one of the files does not exist...
        ###                geocode_master(folder, geocode_inputfile, dem_inputfile, doris_dir)
                # this function geocode the bursts of a master file, based on a DEM
                # Run the create DEM command

                if (not (self.parallel)):
                    os.chdir(path)

                if not dem_inputfile == False:
                    if not doris_dir:
                        command = 'doris ' + dem_inputfile
                        job_list1.append([path, command])
                        if (not (self.parallel)):
                            os.system(command)
                    else:
                        command = doris_dir + ' ' + dem_inputfile
                        job_list1.append([path, command])
                        if (not (self.parallel)):
                            os.system(command)
            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list1)

        self.read_res()

        for date in dates:
            for burst in self.stack[date].keys():

                resultfile = copy.deepcopy(self.stack[date][burst]['ifgs'])

                # Add the slant2height information. This is meant to fake the doris script
                sl2h_dat = collections.OrderedDict()
                sl2h_dat['Method'] = 'schwabisch'
                sl2h_dat['Data_output_file'] = 'dem_radar.raw'
                sl2h_dat['Data_output_format'] = 'real4'
                sl2h_dat['First_line (w.r.t. original_master)'] = resultfile.processes['comp_refdem'][
                    'First_line (w.r.t. original_master)']
                sl2h_dat['Last_line (w.r.t. original_master)'] = resultfile.processes['comp_refdem'][
                    'Last_line (w.r.t. original_master)']
                sl2h_dat['First_pixel (w.r.t. original_master)'] = resultfile.processes['comp_refdem'][
                    'First_pixel (w.r.t. original_master)']
                sl2h_dat['Last_pixel (w.r.t. original_master)'] = resultfile.processes['comp_refdem'][
                    'Last_pixel (w.r.t. original_master)']
                sl2h_dat['Multilookfactor_azimuth_direction'] = resultfile.processes['comp_refdem'][
                    'Multilookfactor_azimuth_direction']
                sl2h_dat['Multilookfactor_range_direction'] = resultfile.processes['comp_refdem'][
                    'Multilookfactor_range_direction']
                sl2h_dat['Ellipsoid (name,a,b)'] = 'WGS84 6.37814e+06 6.35675e+06'

                self.stack[date][burst]['ifgs'].processes['slant2h'] = sl2h_dat
                self.stack[date][burst]['ifgs'].process_control['slant2h'] = '1'
                self.stack[date][burst]['ifgs'].process_timestamp['slant2h'] = ''
        self.update_res()

        for date in dates:
            for burst in self.stack[date].keys():
                path = self.burst_path(date,burst)
                geocode_inputfile = os.path.join(self.input_files, 'input.geocode')
                # Generate lat / lon files
                if not doris_dir:
                    command = 'doris ' + geocode_inputfile
                    job_list2.append([path, command])
                    if (not (self.parallel)):
                        os.system(command)
                else:
                    command = doris_dir + ' ' + geocode_inputfile
                    job_list2.append([path, command])
                    if (not (self.parallel)):
                        os.system(command)

            if (self.parallel):
                jobs = Jobs(self.nr_of_jobs, self.doris_parameters)
                jobs.run(job_list2)

        self.concatenate('phi.raw', 'phi.raw',dt=np.dtype('float32'))
        self.concatenate('lam.raw', 'lam.raw',dt=np.dtype('float32'))
        self.concatenate('dem_radar.raw', 'dem_radar.raw', dt=np.dtype('float32'))
