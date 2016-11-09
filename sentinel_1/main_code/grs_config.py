'''
	GrsConfig defines paths that are local to the source tree.
	They are copied into DorisParameters for use in Doris python scripts
'''

class GrsConfig(object):

    def __init__(self):
        self.source_path = '/home/gert/software/doris/Doris_s1'
        self.doris_path = '/usr/local/bin/doris'
        self.cpxfiddle_path = '/usr/local/bin/cpxfiddle'
        self.job_handler_script = self.source_path + "/sentinel_1/main_code/jobHandlerScript"
	self.function_path = self.source_path + "/sentinel_1/functions/"
