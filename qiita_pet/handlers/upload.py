from tornado.web import authenticated, RequestHandler

from os.path import isdir, join, exists
from os import makedirs, listdir

from shutil import copyfileobj, rmtree

from qiita_pet.handlers.base_handlers import BaseHandler
from qiita_db.util import get_user_fp


class UploadFileHandler(BaseHandler):
    # """ main upload class
    # based on
    # https://github.com/23/resumable.js/blob/master/samples/Backend%20on%20PHP.md
    # """

    @authenticated
    def post(self):
        resumableIdentifier = self.get_argument('resumableIdentifier')
        resumableFilename = self.get_argument('resumableFilename')
        resumableChunkNumber = int(self.get_argument('resumableChunkNumber'))
        resumableTotalChunks = int(self.get_argument('resumableTotalChunks'))
        file_type = self.get_argument('file_type')
        data = self.request.files['file'][0]['body']

        fp = join(get_user_fp(self.current_user), file_type,
                  resumableIdentifier)
        # creating temporal folder for upload
        if not isdir(fp):
            makedirs(fp)
        dfp = join(fp, '%s.part.%d' % (resumableFilename,
                                       resumableChunkNumber))

        # writting the output file
        with open(dfp, 'wb') as f:
            f.write(bytes(data))

        # validating if all files have been uploaded
        num_files = len([n for n in listdir(fp)])
        if resumableTotalChunks == num_files:
            # creating final destination
            ffp = join(get_user_fp(self.current_user), file_type,
                       resumableFilename)
            with open(ffp, 'wb') as f:
                for c in range(1, resumableTotalChunks+1):
                    chunk = join(fp, '%s.part.%d' % (resumableFilename, c))
                    copyfileobj(open(chunk, 'rb'), f)

                # deleting the tmp folder with contents and finish file
                rmtree(fp)
                self.set_status(200)

    @authenticated
    def get(self):
        """ this is the first point of entry into the upload service

        this should either set the status as 400 (error) so the file/chunk is
        sent via post or 200 (valid) to not send the file
        """

        # the idea of using path_vals[1] and then [0] is so we have all email
        # servers under the same path as it could be useful in the future
        fp = get_user_fp(self.current_user)
        if not isdir(fp):
            makedirs(fp)

        dfp = join(fp, self.get_argument('resumableFilename') + '.part.' +
                   self.get_argument('resumableChunkNumber'))

        if exists(dfp):
            self.set_status(200)
        else:
            self.set_status(400)
