# -----------------------------------------------------------------------------
# Copyright (c) 2014--, The Qiita Development Team.
#
# Distributed under the terms of the BSD 3-clause License.
#
# The full license is in the file LICENSE, distributed with this software.
# -----------------------------------------------------------------------------
from smtplib import SMTP, SMTP_SSL, SMTPException
from future import standard_library
from functools import wraps
from os.path import dirname
from git import Repo
from git.exc import InvalidGitRepositoryError

from qiita_core.qiita_settings import qiita_config, r_client
from qiita_pet import __version__ as qiita_pet_lib_version
import qiita_db as qdb

with standard_library.hooks():
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

from time import time, mktime
from imaplib import IMAP4_SSL
from email import message_from_string
from email.utils import parsedate


def read_last_email_to_admin(sent_from, sent_to, subject, age_threshold, message_text):
    #TODO: may need a non_SSL option
    #TODO: going to need IMAP environment variables for this thing
    #qiita_config.smtp_host, qiita_config.smtp_port
    mail = IMAP4_SSL('imap.gmail.com')
    mail.login('do.not.test.meeh@gmail.com','password')
    mail.select('inbox')

    #fetch and search both return tuples, with the first value
    #being a text status message ('OK') and the second value
    #being the result value. Ignore status and take value,
    #assuming true errors will return Exceptions.
    latest_email_index = mail.search(None, 'ALL')[1][0][-1]
    results = mail.fetch(latest_email_index, '(RFC822)')[1][0][1]

    msg = message_from_string(results)

    error_list = []

    if msg['from'] != sent_from:
        error_list.append("sender does not match (%s != %s)" %
                (msg['from'], sent_from))
    if msg['to'] != sent_to:
        error_list.append("recipient does not match (%s != %s)" %
                (msg['to'], sent_to))
    if msg['subject'] != subject:
        error_list.append("subject lines do not match (%s != %s)" %
                (msg['subject'], subject))

    #calculate difference in time from when the last message was sent
    #until now, in hours. If time exceeds threshold, accumulate an
    #error message.
    delta = time() - mktime(parsedate(msg['date']))
    delta = delta / (60.0 ** 2)
    if delta > age_threshold:
        error_list.append("Message is too old (%f hours)." % delta)

    message_body = None

    if msg.is_multipart():
        for payload in msg.get_payload():
            if payload.get_content_type() == 'text/plain':
                message_body = payload.get_payload()
                break
    else:
        message_body = msg.get_payload()

    if message_body != message_text:
        error_list.append("message bodies do not match (%s != %s)" %
                (message_body, message_text))

    if message_body:
        return False, message_body

    return True


def send_email(to, subject, body):
    # create email
    msg = MIMEMultipart()
    msg['From'] = qiita_config.smtp_email
    msg['To'] = to
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    # connect to smtp server, using ssl if needed
    if qiita_config.smtp_ssl:
        smtp = SMTP_SSL()
    else:
        smtp = SMTP()
    smtp.set_debuglevel(False)
    smtp.connect(qiita_config.smtp_host, qiita_config.smtp_port)
    # try tls, if not available on server just ignore error
    try:
        smtp.starttls()
    except SMTPException:
        pass
    smtp.ehlo_or_helo_if_needed()

    if qiita_config.smtp_user:
        smtp.login(qiita_config.smtp_user, qiita_config.smtp_password)

    # send email
    try:
        smtp.sendmail(qiita_config.smtp_email, to, msg.as_string())
    except Exception:
        raise RuntimeError("Can't send email!")
    finally:
        smtp.close()

def is_test_environment():
    """Checks if Qiita is running in a test environment

    Returns
    -------
    bool
        Whether Qiita is running in a test environment or not

    Notes
    -----
    Qiita is running in a test environment if:
        - It is connected to a test database, AND
        - The config file indicates that this is a test environment
    """
    # Check that we are not in a production environment
    conn_handler = qdb.sql_connection.SQLConnectionHandler()
    # It is possible that we are connecting to a production database
    test_db = conn_handler.execute_fetchone("SELECT test FROM settings")[0]
    # Or the loaded configuration file belongs to a production environment
    return qiita_config.test_environment and test_db


def qiita_test_checker(test=False):
    """Decorator that allows the execution of all methods in a test class only
    and only if Qiita is set up to work in a test environment.

    Parameters
    ----------
    test : bool, optional
        If True it will raise a RuntimeError error

    Raises
    ------
    RuntimeError
        If Qiita is set up to work in a production environment
    """
    def class_modifier(cls):
        if not is_test_environment() or test:
            raise RuntimeError("Working in a production environment. Not "
                               "executing the tests to keep the production "
                               "database safe.")

        # Now, we decorate the setup and teardown functions
        class DecoratedClass(cls):
            def setUp(self):
                super(DecoratedClass, self).setUp()
                self.conn_handler = qdb.sql_connection.SQLConnectionHandler()

            @classmethod
            @qdb.environment_manager.reset_test_database
            def tearDownClass(cls):
                pass

        return DecoratedClass
    return class_modifier


def execute_as_transaction(func):
    """Decorator to make a method execute inside a transaction"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        from qiita_db.sql_connection import TRN
        with TRN:
            return func(*args, **kwargs)
    return wrapper


def get_qiita_version():
    """Returns the Qiita version and Git sha if present

    Returns
    ------
    tuple (version, sha)
        The Qiita version and SHA. SHA can be an empty string.
    """
    # the actual repo is the abspath of the current file without
    # qiita_core
    git_repo_path = dirname(dirname(__file__))

    try:
        repo = Repo(git_repo_path)
        sha = repo.active_branch.commit.hexsha
    except (InvalidGitRepositoryError, TypeError):
        sha = ''

    return (qiita_pet_lib_version, sha)


def get_release_info(study_status='public'):
    """Returns the study status release MD5

    Parameters
    ----------
    study_status : str, optional
        The study status to search for. Note that this should always be set
        to 'public' but having this exposed helps with testing. The other
        options are 'private' and 'sandbox'

    Returns
    ------
    str, str, str
        The release MD5, filepath and timestamp
    """
    portal = qiita_config.portal
    md5sum = r_client.get('%s:release:%s:md5sum' % (portal, study_status))
    filepath = r_client.get('%s:release:%s:filepath' % (portal, study_status))
    timestamp = r_client.get('%s:release:%s:time' % (portal, study_status))
    if md5sum is None:
        md5sum = ''
    if filepath is None:
        filepath = ''
    if timestamp is None:
        timestamp = ''

    return md5sum, filepath, timestamp
