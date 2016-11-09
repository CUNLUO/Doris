
# File created by Gert Mulder, TU Delft, the Netherlands
#

# This function will create the needed files for a datastack based on input information from the datastack.
import xml.etree.ElementTree as ET
import os
import pickle

# Test data
# settings_table = '/home/gert/software/doris/Doris_s1/sentinel_1/functions/inputfile.xml'
# inputfile_folder = '/media/gert/Data/dem/test/'
# sensor = 'sentinel-1'
# dem_info = inputfile_folder + 'output.dem.var'

class CreateInputFiles:
    # Input for the class to create inputfiles are:
    # - dem_info > variable or file with variable with information on the dem.
    # - settings_table > this table includes the general settings for different sensors. You can either choose one of
    #                       the predefined sensors or create a custom one.
    # - sensor > select the predefined sensor of your choice
    # - Other settings are mainly related to how much output information you want from the program. Also you can define
    #   the amount of memory used by the program.


    def __init__(self, dem_info ,inputfile_folder ,settings_table, sensor):
        tree = ET.parse(settings_table)
        settings = tree.getroot()

        xml_data = settings.find('.' + sensor)
        header_data = xml_data.find('.header_settings')
        dem_info = open(dem_info, 'r')
        dem_var = pickle.load(dem_info)

        inputfilenames = ['coarsecorr', 'coarseorb', 'coherence', 'comprefdem', 'comprefpha', 'coregpm',
                          'dembased', 'finecoreg', 'geocode', 'interferogram', 'resample', 'subtrefdem', 'subtrefpha',
                          'unwrap']

        for filename in inputfilenames:
            # Create file
            inputfilename = os.path.join(inputfile_folder, 'input.' + filename)
            txtfile = open(inputfilename, 'w')

            # Load xml data for processing step
            process = xml_data.find('./' + filename + '/PROCESS')
            process_data = xml_data.find('.' + filename)

            # Write to file
            txtfile = CreateInputFiles.header(txtfile, header_data, process)
            txtfile = CreateInputFiles.create_inputfiles(txtfile, process_data, dem_var)

            # Close file
            txtfile.close()

    @staticmethod
    def create_inputfiles(txtfile, process_data, dem_var):
        # This functions calls the different inputfile creation scripts.

        for node in process_data:
            if not node.tag == 'PROCESS':
                if node.attrib['c'] == 'on':
                    c = 'c '
                else:
                    c = ''

                if 'var' in node.attrib and node.attrib['comment']:
                    txtfile.write(c + node.tag.ljust(20) + '\t' + dem_var[node.attrib['var']].ljust(20) + '\t // ' + node.attrib['comment'] + '\n')
                elif not 'var' in node.attrib and node.attrib['comment']:
                    txtfile.write(c + node.tag.ljust(20) + '\t' + node.text.ljust(20) + '\t // ' + node.attrib['comment'] + '\n')
                elif 'var' in node.attrib and not node.attrib['comment']:
                    txtfile.write(c + node.tag.ljust(20) + '\t' + dem_var[node.attrib['var']].ljust(20) + '\n')
                elif not 'var' in node.attrib and not node.attrib['comment']:
                    txtfile.write(c + node.tag.ljust(20) + '\t' + node.text.ljust(20) + '\n')

        return txtfile

    @staticmethod
    def header(txtfile, header_data, process):
        # Function to write header

        txtfile.write("c Inputfile created by Doris 5.0" + '\n')
        txtfile.write("c         " + "___".ljust(len(process.text) + 3) + "___ \n")
        txtfile.write("comment   ___" + process.text + "___ \n")
        txtfile.write("c                             \n")

        for node in header_data:
            if node.tag == 'PROCESS':
                txtfile.write("c \n")
                txtfile.write("PROCESS ".ljust(15) + process.text + " \n")
                txtfile.write("c \n")
            else:
                if node.attrib['comment']:
                    txtfile.write(node.tag.ljust(20) + '\t' + node.text.ljust(20) + '\t // ' + node.attrib['comment'] + '\n')

        txtfile.write("c                             \n")

        return txtfile