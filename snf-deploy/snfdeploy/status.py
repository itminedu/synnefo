# Copyright (C) 2010-2014 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import ConfigParser
import os
import sys
from snfdeploy import constants
from snfdeploy import config
from snfdeploy import filelocker

status = sys.modules[__name__]


def _create_section(section):
    if not status.cfg.has_section(section):
        status.cfg.add_section(section)


def _check(section, option):
    try:
        return status.cfg.get(section, option, True)
    except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
        return None


def _update(section, option, value):
    _create_section(section)
    status.cfg.set(section, option, value)
    if config.force or not config.dry_run:
        _write()


def _write():
    with filelocker.lock("%s.lock" % status.statusfile, filelocker.LOCK_EX):
        with open(status.statusfile, 'wb') as configfile:
            status.cfg.write(configfile)


def update(component):
    section = component.node.ip
    option = component.__class__.__name__
    _update(section, option, constants.VALUE_OK)


def check(component):
    section = component.node.ip
    option = component.__class__.__name__
    return _check(section,  option)


def init():
    status.state_dir = config.state_dir
    status.cfg = ConfigParser.ConfigParser()
    status.cfg.optionxform = str
    status.statusfile = os.path.join(config.state_dir, constants.STATUS_FILE)
    status.cfg.read(status.statusfile)
