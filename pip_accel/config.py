# Configuration defaults for the pip accelerator.
#
# Author: Peter Odding <peter.odding@paylogic.eu>
# Last Change: November 22, 2014
# URL: https://github.com/paylogic/pip-accel

"""
:py:mod:`pip_accel.config` - Configuration handling
===================================================

This module defines the :py:class:`Config` class which is used throughout the
pip accelerator. At runtime an instance of :py:class:`Config` is created and
passed down like this:

.. digraph:: config_dependency_injection

   node [fontsize=10, shape=rect]

   PipAccelerator -> BinaryDistributionManager
   BinaryDistributionManager -> CacheManager
   CacheManager -> LocalCacheBackend
   CacheManager -> S3CacheBackend
   BinaryDistributionManager -> SystemPackageManager

The :py:class:`.PipAccelerator` class receives its configuration object from
its caller. Usually this will be :py:func:`.main()` but when pip-accel is used
as a Python API the person embedding or extending pip-accel is responsible for
providing the configuration object. This is intended as a form of `dependency
injection`_ that enables non-default configurations to be injected into
pip-accel.

Support for configuration files
-------------------------------

You can use a configuration file to permanently configure certain options of
pip-accel. If ``/etc/pip-accel.conf`` and/or ``~/.pip-accel/pip-accel.conf``
exist they are automatically loaded. You can also set the environment variable
``$PIP_ACCEL_CONFIG`` to load a configuration file in a non-default location.
If all three files exist the system wide file is loaded first, then the user
specific file is loaded and then the file set by the environment variable is
loaded (duplicate settings are overridden by the configuration file that's
loaded last).

Here is an example of the available options:

        .. code-block:: ini

           [pip-accel]
           auto-install = on
           data-directory = ~/.pip-accel
           download-cache = ~/.pip/download-cache
           s3-bucket = my-shared-pip-accel-binary-cache
           s3-prefix = ubuntu-trusty-amd64

Note that the configuration options shown above are just examples, they are not
meant to represent the configuration defaults.

.. _dependency injection: http://en.wikipedia.org/wiki/Dependency_injection
"""

# Standard library modules.
import logging
import os
import os.path
import sys

# Modules included in our package.
from pip_accel.compat import configparser

# External dependencies.
from cached_property import cached_property
from humanfriendly import coerce_boolean, parse_path

# Initialize a logger for this module.
logger = logging.getLogger(__name__)

# The locations of the user specific and system wide configuration files.
LOCAL_CONFIG = '~/.pip-accel/pip-accel.conf'
GLOBAL_CONFIG = '/etc/pip-accel.conf'

class Config(object):

    """Configuration of the pip accelerator."""

    def __init__(self, load_configuration_files=True, load_environment_variables=True):
        """
        Initialize the configuration of the pip accelerator.

        :param load_configuration_files: If this is ``True`` (the default) then
                                         configuration files in known locations
                                         are automatically loaded.
        :param load_environment_variables: If this is ``True`` (the default) then
                                           environment variables are used to
                                           initialize the configuration.
        """
        self.configuration = {}
        self.environment = os.environ if load_environment_variables else {}
        if load_configuration_files:
            for filename in self.available_configuration_files:
                self.load_configuration_file(filename)

    @cached_property
    def available_configuration_files(self):
        """A list of strings with the absolute pathnames of the available configuration files."""
        known_files = [GLOBAL_CONFIG, LOCAL_CONFIG, self.environment.get('PIP_ACCEL_CONFIG')]
        absolute_paths = [parse_path(pathname) for pathname in known_files if pathname]
        return [pathname for pathname in absolute_paths if os.path.isfile(pathname)]

    def load_configuration_file(self, configuration_file):
        """
        Load configuration defaults from a configuration file.

        :param configuration_file: The pathname of a configuration file (a
                                   string).
        :raises: :py:exc:`Exception` when the configuration file cannot be
                 loaded.
        """
        configuration_file = parse_path(configuration_file)
        logger.debug("Loading configuration file: %s", configuration_file)
        parser = configparser.RawConfigParser()
        files_loaded = parser.read(configuration_file)
        if len(files_loaded) != 1:
            msg = "Failed to load configuration file! (%s)"
            raise Exception(msg % configuration_file)
        elif not parser.has_section('pip-accel'):
            msg = "Missing 'pip-accel' section in configuration file! (%s)"
            raise Exception(msg % configuration_file)
        else:
            self.configuration.update(parser.items('pip-accel'))

    def get(self, environment_variable, configuration_option, default=None):
        """
        Internal shortcut to get a configuration option's value.

        :param environment_variable: The name of the environment variable (a
                                     string).
        :param configuration_option: The name of the option in the
                                     configuration file (a string).
        :param default: The default value.
        :returns: The value of the environment variable or configuration file
                  option or the default value.
        """
        return (self.environment.get(environment_variable) or
                self.configuration.get(configuration_option) or
                default)

    @cached_property
    def cache_format_revision(self):
        """
        The revision of the binary distribution cache format in use (an integer).

        This number is encoded in the directory name of the binary cache so
        that multiple revisions can peacefully coexist. When pip-accel breaks
        backwards compatibility this number is bumped so that pip-accel starts
        using a new directory.
        """
        return 7

    @cached_property
    def download_cache(self):
        """
        The absolute pathname of pip's download cache directory (a string).

        - Environment variable: ``$PIP_DOWNLOAD_CACHE``
        - Configuration option: ``download-cache``
        - Default: ``~/.pip/download-cache``
        """
        return parse_path(self.get(environment_variable='PIP_DOWNLOAD_CACHE',
                                   configuration_option='download-cache',
                                   default='~/.pip/download-cache'))

    @cached_property
    def source_index(self):
        """
        The absolute pathname of pip-accel's source index directory (a string).

        This is the ``sources`` subdirectory of :py:data:`data_directory`.
        """
        return os.path.join(self.data_directory, 'sources')

    @cached_property
    def binary_cache(self):
        """
        The absolute pathname of pip-accel's binary cache directory (a string).

        This is the ``binaries`` subdirectory of :py:data:`data_directory`.
        """
        return os.path.join(self.data_directory, 'binaries')

    @cached_property
    def data_directory(self):
        """
        The absolute pathname of the directory where pip-accel's data files are stored (a string).

        - Environment variable: ``$PIP_ACCEL_CACHE``
        - Configuration option: ``data-directory``
        - Default: ``/var/cache/pip-accel`` if running as ``root``, ``~/.pip-accel`` otherwise.
        """
        return parse_path(self.get(environment_variable='PIP_ACCEL_CACHE',
                                   configuration_option='data-directory',
                                   default='/var/cache/pip-accel' if os.getuid() == 0 else '~/.pip-accel'))

    @cached_property
    def on_debian(self):
        """``True`` if running on a Debian derived system, ``False`` otherwise."""
        return os.path.exists('/etc/debian_version')

    @cached_property
    def install_prefix(self):
        """
        The absolute pathname of the installation prefix to use (a string).

        This property is based on :py:data:`sys.prefix` except that when
        :py:data:`sys.prefix` is ``/usr`` and we're running on a Debian derived
        system ``/usr/local`` is used instead.

        The reason for this is that on Debian derived systems only apt (dpkg)
        should be allowed to touch files in ``/usr/lib/pythonX.Y/dist-packages``
        and ``python setup.py install`` knows this (see the ``posix_local``
        installation scheme in ``/usr/lib/pythonX.Y/sysconfig.py`` on Debian
        derived systems). Because pip-accel replaces ``python setup.py
        install`` it has to replicate this logic. Inferring all of this from
        the :py:mod:`sysconfig` module would be nice but that module wasn't
        available in Python 2.6.
        """
        return '/usr/local' if sys.prefix == '/usr' and self.on_debian else sys.prefix

    @cached_property
    def python_executable(self):
        """The absolute pathname of the Python executable (a string)."""
        return sys.executable or os.path.join(self.install_prefix, 'bin', 'python')

    @cached_property
    def auto_install(self):
        """
        ``True`` if automatic installation of missing system packages is
        enabled, ``False`` if it disabled, ``None`` otherwise (in this case the
        user will be prompted at the appropriate time). The default is ``None``.

        - Environment variable:  ``$PIP_ACCEL_AUTO_INSTALL`` (refer to
          :py:func:`~humanfriendly.coerce_boolean()` for details on how the
          value of the environment variable is interpreted).
        - Configuration option: ``auto-install`` (also parsed using
          :py:func:`~humanfriendly.coerce_boolean()`).
        - Default: 
        """
        value = self.get(environment_variable='PIP_ACCEL_AUTO_INSTALL',
                         configuration_option='auto-install')
        if value is not None:
            return coerce_boolean(value)

    @cached_property
    def s3_cache_bucket(self):
        """
        The name of the Amazon S3 bucket where binary distribution archives are
        cached (a string or ``None``). You can set this configuration option
        using the environment variable ``$PIP_ACCEL_S3_BUCKET``.

        For details please refer to the :py:mod:`pip_accel.caches.s3` module.
        """
        return self.get(environment_variable='PIP_ACCEL_S3_BUCKET',
                        configuration_option='s3-bucket')

    @cached_property
    def s3_cache_prefix(self):
        """
        The cache prefix for binary distribution archives in the Amazon S3
        bucket (a string).  You can set this configuration option using the
        environment variable ``$PIP_ACCEL_S3_PREFIX``.

        For details please refer to the :py:mod:`pip_accel.caches.s3` module.
        """
        return self.get(environment_variable='PIP_ACCEL_S3_PREFIX',
                        configuration_option='s3-prefix')
