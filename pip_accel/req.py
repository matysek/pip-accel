# Accelerator for pip, the Python package manager.
#
# Author: Peter Odding <peter.odding@paylogic.com>
# Last Change: October 31, 2015
# URL: https://github.com/paylogic/pip-accel

"""
Simple wrapper for pip and pkg_resources `Requirement` objects.

After downloading the specified requirement(s) pip reports a "requirement set"
to pip-accel. In the past pip-accel would summarize this requirement set into a
list of tuples, where each tuple would contain a requirement's project name,
version and source directory (basically only the information required by
pip-accel remained).

Recently I've started using pip-accel as a library in another project I'm
working on (not yet public) and in that project I am very interested in whether
a given requirement is a direct or transitive requirement. Unfortunately
pip-accel did not preserve this information.

That's when I decided that next to pip's :class:`pip.req.InstallRequirement`
and setuptools' :class:`pkg_resources.Requirement` I would introduce yet
another type of requirement object... It's basically just a summary of the
other two types of requirement objects and it also provides access to the
original requirement objects (for those who are interested; the interfaces are
basically undocumented AFAIK).
"""

# Standard library modules.
import glob
import os
import re
import time

# Modules included in our package.
from pip_accel.exceptions import UnknownDistributionFormat

# External dependencies.
from cached_property import cached_property
from pip._vendor.distlib.util import ARCHIVE_EXTENSIONS
from pip._vendor.pkg_resources import find_distributions
from pip.req import InstallRequirement


class Requirement(object):

    """Simple wrapper for the requirement objects defined by pip and setuptools."""

    def __init__(self, config, requirement):
        """
        Initialize a requirement object.

        :param config: A :class:`~pip_accel.config.Config` object.
        :param requirement: A :class:`pip.req.InstallRequirement` object.
        """
        self.config = config
        self.pip_requirement = requirement
        self.setuptools_requirement = requirement.req

    def __repr__(self):
        """Generate a human friendly representation of a requirement object."""
        return "Requirement(name=%r, version=%r)" % (self.name, self.version)

    @cached_property
    def name(self):
        """
        The name of the Python package (a string).

        This is the name used to register a package on PyPI and the name
        reported by commands like ``pip freeze``. Based on
        :attr:`pkg_resources.Requirement.project_name`.
        """
        return self.setuptools_requirement.project_name

    @cached_property
    def version(self):
        """The version of the package that ``pip`` wants to install (a string)."""
        if self.is_wheel:
            return self.wheel_metadata.version
        else:
            return self.sdist_metadata['Version']

    @cached_property
    def related_archives(self):
        """
        Try to find the source distribution archive(s) for this requirement.

        Returns a list of pathnames (strings).

        This property is very new in pip-accel and its logic may need some time
        to mature. For now any misbehavior by this property shouldn't be too
        much of a problem because the pathnames reported by this property are
        only used for cache invalidation (see :attr:`last_modified`).
        """
        # Escape the requirement's name for use in a regular expression.
        name_pattern = escape_name(self.name)
        # Escape the requirement's version for in a regular expression.
        version_pattern = re.escape(self.version)
        # Create a regular expression that matches any of the known source
        # distribution archive extensions.
        extension_pattern = '|'.join(re.escape(ext) for ext in ARCHIVE_EXTENSIONS if ext != '.whl')
        # Compose the regular expression pattern to match filenames of source
        # distribution archives in the local source index directory.
        pattern = '^%s-%s(%s)$' % (name_pattern, version_pattern, extension_pattern)
        # Compile the regular expression for case insensitive matching.
        compiled_pattern = re.compile(pattern, re.IGNORECASE)
        # Find the matching source distribution archives.
        return [os.path.join(self.config.source_index, fn)
                for fn in os.listdir(self.config.source_index)
                if compiled_pattern.match(fn)]

    @cached_property
    def last_modified(self):
        """
        Try to find the last modified time of the requirement's source distribution archive(s).

        Returns a number.

        Based on :attr:`related_archives`. If no related archives are found
        the current time is reported. In the balance between not invalidating
        cached binary distributions enough and invalidating them too
        frequently, this property causes the latter to happen.
        """
        mtimes = list(map(os.path.getmtime, self.related_archives))
        return max(mtimes) if mtimes else time.time()

    @cached_property
    def source_directory(self):
        """
        The pathname of the directory containing the unpacked source distribution (a string).

        This is the directory that contains a ``setup.py`` script. Based on
        :attr:`pip.req.InstallRequirement.source_dir`.
        """
        return self.pip_requirement.source_dir

    @cached_property
    def is_wheel(self):
        """
        :data:`True` when the requirement is a wheel, :data:`False` otherwise.

        .. note:: To my surprise it seems to be non-trivial to determine
                  whether a given :class:`pip.req.InstallRequirement` object
                  produced by pip's internal Python API concerns a source
                  distribution or a wheel distribution.

                  There's a :class:`pip.req.InstallRequirement.is_wheel`
                  property but I'm currently looking at a wheel distribution
                  whose ``is_wheel`` property returns :data:`None`, apparently
                  because the requirement's ``url`` property is also :data:`None`.

                  Whether this is an obscure implementation detail of pip or
                  caused by the way pip-accel invokes pip, I really can't tell
                  (yet).
        """
        probably_sdist = os.path.isfile(os.path.join(self.source_directory, 'setup.py'))
        probably_wheel = len(glob.glob(os.path.join(self.source_directory, '*.dist-info', 'WHEEL'))) > 0
        if probably_wheel and not probably_sdist:
            return True
        elif probably_sdist and not probably_wheel:
            return False
        elif probably_sdist and probably_wheel:
            variables = dict(requirement=self.setuptools_requirement,
                             directory=self.source_directory)
            raise UnknownDistributionFormat("""
                The unpacked distribution of {requirement} in {directory} looks
                like a source distribution and a wheel distribution, I'm
                confused!
            """, **variables)
        else:
            variables = dict(requirement=self.setuptools_requirement,
                             directory=self.source_directory)
            raise UnknownDistributionFormat("""
                The unpacked distribution of {requirement} in {directory}
                doesn't look like a source distribution and also doesn't look
                like a wheel distribution, I'm confused!
            """, **variables)

    @cached_property
    def is_transitive(self):
        """
        Whether the dependency is transitive (indirect).

        :data:`True` when the requirement is a transitive dependency (a
        dependency of a dependency) or :data:`False` when the requirement is a
        direct dependency (specified on pip's command line or in a
        ``requirements.txt`` file). Based on
        :attr:`pip.req.InstallRequirement.comes_from`.
        """
        return isinstance(self.pip_requirement.comes_from, InstallRequirement)

    @cached_property
    def is_direct(self):
        """The opposite of :attr:`Requirement.is_transitive`."""
        return not self.is_transitive

    @cached_property
    def is_editable(self):
        """
        Whether the requirement should be installed in editable mode.

        :data:`True` when the requirement is to be installed in editable mode
        (i.e. setuptools "develop mode"). Based on
        :attr:`pip.req.InstallRequirement.editable`.
        """
        return self.pip_requirement.editable

    @cached_property
    def sdist_metadata(self):
        """Get the distribution metadata of an unpacked source distribution."""
        if self.is_wheel:
            raise TypeError("Requirement is not a source distribution!")
        return self.pip_requirement.pkg_info()

    @cached_property
    def wheel_metadata(self):
        """Get the distribution metadata of an unpacked wheel distribution."""
        if not self.is_wheel:
            raise TypeError("Requirement is not a wheel distribution!")
        for distribution in find_distributions(self.source_directory):
            return distribution
        msg = "pkg_resources didn't find a wheel distribution in %s!"
        raise Exception(msg % self.source_directory)

    def __str__(self):
        """Render a human friendly string describing the requirement."""
        return "%s (%s)" % (self.name, self.version)


def escape_name(requirement_name):
    """
    Escape a requirement's name for use in a regular expression.

    This backslash-escapes all non-alphanumeric characters and replaces dashes
    and underscores with a character class that matches a dash or underscore
    (effectively treating dashes and underscores equivalently).

    :param requirement_name: The name of the requirement (a string).
    :returns: The requirement's name as a regular expression (a string).
    """
    return re.sub('[^A-Za-z0-9]', escape_name_callback, requirement_name)


def escape_name_callback(match):
    """
    Used by :func:`escape_name()` to treat dashes and underscores as equivalent.

    :param match: A regular expression match object that captured a single character.
    :returns: A regular expression string that matches the captured character.
    """
    character = match.group(0)
    return '[-_]' if character in ('-', '_') else r'\%s' % character
