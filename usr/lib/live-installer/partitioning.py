#!/usr/bin/python3
# coding: utf-8

import os
import re
import sys
import subprocess
from collections import defaultdict

from gi.repository import Gtk, Gdk
import parted
import gettext

gettext.install("live-installer", "/usr/share/gooroom/locale")

def shell_exec(command):
    return subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, universal_newlines=True)

def getoutput(command):
    return shell_exec(command).stdout.read().strip()

(IDX_PART_PATH,
 IDX_PART_TYPE,
 IDX_PART_DESCRIPTION,
 IDX_PART_FORMAT_AS,
 IDX_PART_MOUNT_AS,
 IDX_PART_SIZE,
 IDX_PART_FREE_SPACE,
 IDX_PART_OBJECT,
 IDX_PART_DISK) = list(range(9))

def is_efi_supported():
    # Are we running under with efi ?
    os.system("modprobe efivars >/dev/null 2>&1")
    return os.path.exists("/proc/efi") or os.path.exists("/sys/firmware/efi")

def path_exists(*args):
    return os.path.exists(os.path.join(*args))

TMP_MOUNTPOINT = '/tmp/live-installer/tmpmount'
RESOURCE_DIR = '/usr/share/live-installer/'

EFI_MOUNT_POINT = '/boot/efi'
SWAP_MOUNT_POINT = 'swap'


with open(RESOURCE_DIR + 'disk-partitions.html') as f:
    DISK_TEMPLATE = f.read()
    # cut out the single partition (skeleton) block
    PARTITION_TEMPLATE = re.search('CUT_HERE([\s\S]+?)CUT_HERE', DISK_TEMPLATE, re.MULTILINE).group(1)
    # delete the skeleton from original
    DISK_TEMPLATE = DISK_TEMPLATE.replace(PARTITION_TEMPLATE, '')
    # duplicate all { or } in original CSS so they don't get interpreted as part of string formatting
    DISK_TEMPLATE = re.sub('<style>[\s\S]+?</style>', lambda match: match.group().replace('{', '{{').replace('}', '}}'), DISK_TEMPLATE)


def build_partitions(_installer):
    global installer
    installer = _installer
    installer.window.get_window().set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))  # "busy" cursor
    installer.window.set_sensitive(False)
    print("Starting PartitionSetup()")
    partition_setup = PartitionSetup()
    print("Finished PartitionSetup()")
    if partition_setup.disks:
        installer._selected_disk = partition_setup.disks[0][0]
        print("Loading HTML string")
        #installer.partitions_browser.load_string(partition_setup.get_html(installer._selected_disk), 'text/html', 'UTF-8', 'file:///')
        installer.partitions_browser.load_html(partition_setup.get_html(installer._selected_disk), 'file:///')
    print("Showing the partition screen")
    installer.builder.get_object("scrolled_partitions").show_all()
    installer.builder.get_object("treeview_disks").set_model(partition_setup)
    installer.builder.get_object("treeview_disks").expand_all()
    installer.window.get_window().set_cursor(None)
    installer.window.set_sensitive(True)
    build_grub_partitions()

def update_html_preview(selection):
    model, row = selection.get_selected()
    try: disk = model[row][IDX_PART_DISK]
    except TypeError as IndexError: return  # no disk is selected or no disk available
    if disk != installer._selected_disk:
        installer._selected_disk = disk
        #installer.partitions_browser.load_string(model.get_html(disk), 'text/html', 'UTF-8', 'file:///')
        installer.partitions_browser.load_html(model.get_html(disk), 'file:///')

def edit_partition_dialog(widget, path, viewcol):
    ''' assign the partition ... '''
    model, iter = installer.builder.get_object("treeview_disks").get_selection().get_selected()
    if not iter: return
    row = model[iter]
    partition = row[IDX_PART_OBJECT]
    if (partition.partition.type != parted.PARTITION_EXTENDED and
        partition.partition.number != -1):
        dlg = PartitionDialog(row[IDX_PART_PATH],
                              row[IDX_PART_MOUNT_AS],
                              row[IDX_PART_FORMAT_AS],
                              row[IDX_PART_TYPE])
        response_is_ok, mount_as, format_as = dlg.show()
        if response_is_ok:
            assign_mount_point(partition, mount_as, format_as)

def assign_mount_point(partition, mount_point, filesystem):
    # Assign it in the treeview
    model = installer.builder.get_object("treeview_disks").get_model()
    for disk in model:
        for part in disk.iterchildren():
            if partition == part[IDX_PART_OBJECT]:
                part[IDX_PART_MOUNT_AS] = mount_point
                part[IDX_PART_FORMAT_AS] = filesystem
            elif mount_point == part[IDX_PART_MOUNT_AS]:
                part[IDX_PART_MOUNT_AS] = ""
                part[IDX_PART_FORMAT_AS] = ""
    # Assign it in our setup
    for part in installer.setup.partitions:
        if part == partition:
            partition.mount_as, partition.format_as = mount_point, filesystem
        elif part.mount_as == mount_point:
            part.mount_as, part.format_as = '', ''
    installer.setup.print_setup()

def partitions_popup_menu(widget, event):
    if event.button != 3: return
    model, iter = installer.builder.get_object("treeview_disks").get_selection().get_selected()
    if not iter: return
    partition = model.get_value(iter, IDX_PART_OBJECT)
    if not partition: return
    partition_type = model.get_value(iter, IDX_PART_TYPE)
    if (partition.partition.type == parted.PARTITION_EXTENDED or
        partition.partition.number == -1 or
        "swap" in partition_type):
        return
    menu = Gtk.Menu()
    menuItem = Gtk.MenuItem(_("Edit"))
    menuItem.connect("activate", edit_partition_dialog, None, None)
    menu.append(menuItem)
    menuItem = Gtk.SeparatorMenuItem()
    menu.append(menuItem)
    menuItem = Gtk.MenuItem(_("Assign to /"))
    menuItem.connect("activate", lambda w: assign_mount_point(partition, '/', 'ext4'))
    menu.append(menuItem)
    menuItem = Gtk.MenuItem(_("Assign to /home"))
    menuItem.connect("activate", lambda w: assign_mount_point(partition, '/home', ''))
    menu.append(menuItem)
    if installer.setup.gptonefi:
        menuItem = Gtk.SeparatorMenuItem()
        menu.append(menuItem)
        menuItem = Gtk.MenuItem(_("Assign to /boot/efi"))
        menuItem.connect("activate", lambda w: assign_mount_point(partition, EFI_MOUNT_POINT, ''))
        menu.append(menuItem)
    menu.show_all()
    menu.popup(None, None, None, None, 0, event.time)

def manually_edit_partitions(widget):
    """ Edit only known disks in gparted, selected one first """
    model, iter = installer.builder.get_object("treeview_disks").get_selection().get_selected()
    preferred = model[iter][-1] if iter else ''  # prefer disk currently selected and show it first in gparted
    disks = ' '.join(sorted((disk for disk,desc in model.disks), key=lambda disk: disk != preferred))
    os.system('umount ' + disks)  # umount disks (if possible) so gparted works out-of-the-box
    os.popen('gparted {} &'.format(disks))

def build_grub_partitions():
    grub_model = Gtk.ListStore(str)
    try: preferred = [p.partition.disk.device.path for p in installer.setup.partitions if p.mount_as == '/'][0]
    except IndexError: preferred = ''
    devices = sorted(list(d[0] for d in installer.setup.partition_setup.disks) +
                     list([_f for _f in (p.name for p in installer.setup.partitions) if _f]))
    if preferred:
        devices.remove(preferred)
        devices.insert(0,preferred)

    for p in devices: grub_model.append([p])
    installer.builder.get_object("combobox_grub").set_model(grub_model)
    installer.builder.get_object("combobox_grub").set_active(0)

class PartitionSetup(Gtk.TreeStore):
    def __init__(self):
        super(PartitionSetup, self).__init__(str,  # path
                                             str,  # type (fs)
                                             str,  # description (OS)
                                             str,  # format to
                                             str,  # mount point
                                             str,  # size
                                             str,  # free space
                                             object,  # partition object
                                             str)  # disk device path
        installer.setup.partitions = []
        installer.setup.partition_setup = self
        self.html_disks, self.html_chunks = {}, defaultdict(list)

        def _get_attached_disks():
            disks = []
            exclude_devices = ['/dev/sr0', '/dev/sr1', '/dev/cdrom', '/dev/dvd', '/dev/fd0']
            try:
                live_device = subprocess.check_output("findmnt -n -o source /run/live/medium", shell=True).split('\n')[0]
                live_device = re.sub('[0-9]+$', '', live_device) # remove partition numbers if any
            except:
                 live_device = None
            if live_device is not None and live_device.startswith('/dev/'):
                exclude_devices.append(live_device)
                print("Excluding %s (detected as the live device)" % live_device)
            lsblk = shell_exec('LC_ALL=en_US.UTF-8 lsblk -rindo TYPE,NAME,RM,SIZE,MODEL | sort -k3,2')
            for line in lsblk.stdout:
                try:
                    elements = line.strip().split(" ", 4)
                    if len(elements) < 4:
                        print("Can't parse blkid output: %s" % elements)
                        continue
                    elif len(elements) < 5:
                        print("Can't find model in blkid output: %s" % elements)
                        type, device, removable, size, model = elements[0], elements[1], elements[2], elements[3], elements[1]
                    else:
                        type, device, removable, size, model = elements
                    device = "/dev/" + device
                    if type == "disk" and device not in exclude_devices:
                        # convert size to manufacturer's size for show, e.g. in GB, not GiB!
                        unit_index = 'BKMGTPEZY'.index(size.upper()[-1])
                        l10n_unit = [_('B'), _('kB'), _('MB'), _('GB'), _('TB'), 'PB', 'EB', 'ZB', 'YB'][unit_index]

                        if (int(float(size[:-1]) * (1024/1000)**unit_index) == 0):
                            continue;

                        size = "%s %s" % (str(int(float(size[:-1]) * (1024/1000)**unit_index)), l10n_unit)

                        model = model.replace("\\x20", " ")
                        description = '{} ({})'.format(model.strip(), size)
                        if int(removable):
                            description = _('Removable:') + ' ' + description
                        disks.append((device, description))
                except Exception as detail:
                    print("Could not parse blkid output: %s (%s)" % (line, detail))
            return disks

        os.popen('mkdir -p ' + TMP_MOUNTPOINT)
        installer.setup.gptonefi = is_efi_supported()
        self.disks = _get_attached_disks()
        print('Disks: ', self.disks)
        already_done_full_disk_format = False
        for disk_path, disk_description in self.disks:
            print("    Analyzing path='%s' description='%s'" % (disk_path, disk_description))
            disk_device = parted.getDevice(disk_path)
            print("      - Found the device...")
            try:
                disk = parted.Disk(disk_device)
                print("      - Found the disk...")
            except Exception as detail:
                print("      - Found an issue while looking for the disk: %s" % detail)
                """
                from frontend.gtk_interface import QuestionDialog
                dialog = QuestionDialog(_("Installation Tool"),
                                        _("No partition table was found on the hard drive: %s. Do you want the installer to create a set of partitions for you? Note: This will ERASE ALL DATA present on this disk.") % disk_description,
                                        None, installer.window)
                """
                dialog = QuestionDialogWithCheckbox(_("Installation Tool"),
                                    _("No partition table was found on the hard drive: %s. Do you want the installer to create a set of partitions for you? Note: This will ERASE ALL DATA present on this disk.") % disk_description,
                                    installer.window)

                response = dialog.run()
                """
                if response == Gtk.ResponseType.YES:
                    if is_backup:
                        pass
                    else:
                        # Format without assigning mount points
                        pass
                elif response == Gtk.ResponseType.NO:
                    # User said No
                    pass
                """
                if response == Gtk.ResponseType.NO:
                    dialog.destroy ()
                    dialog = None

                if not dialog: continue  # the user said No, skip this disk

                try:
                    is_backup = dialog.checkbox.get_active()
                    dialog.destroy ()

                    installer.window.get_window().set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))
                    print("Performing a full disk format")
                    if not already_done_full_disk_format:
                        assign_mount_format = self.full_disk_format(disk_device ,is_backup)
                        already_done_full_disk_format = True
                    else:
                        self.full_disk_format(disk_device, is_backup) # Format but don't assign mount points
                    installer.window.get_window().set_cursor(None)
                    print("Done full disk format")
                    disk = parted.Disk(disk_device)
                    print("Got disk!")
                except Exception as second_exception:
                    installer.window.get_window().set_cursor(None)
                    print("      - Found another issue while looking for the disk: %s" % detail)
                    continue # Something is wrong with this disk, skip it

            disk_iter = self.append(None, (disk_description, '', '', '', '', '', '', None, disk_path))
            print("      - Looking at partitions...")
            free_space_partition = disk.getFreeSpacePartitions()
            print("           -> %d free space partitions" % len(free_space_partition))
            primary_partitions = disk.getPrimaryPartitions()
            print("           -> %d primary partitions" % len(primary_partitions))
            logical_partitions = disk.getLogicalPartitions()
            print("           -> %d logical partitions" % len(logical_partitions))
            raid_partitions = disk.getRaidPartitions()
            print("           -> %d raid partitions" % len(raid_partitions))
            lvm_partitions = disk.getLVMPartitions()
            print("           -> %d LVM partitions" % len(lvm_partitions)) 
            print('free={} pri={} logi={} raid={} lvm={}'.format(free_space_partition, primary_partitions, logical_partitions, raid_partitions, lvm_partitions))
#partition_set = set(free_space_partition + primary_partitions + logical_partitions + raid_partitions + lvm_partitions)
            partition_set = free_space_partition + primary_partitions + logical_partitions + raid_partitions + lvm_partitions

            print("           -> set of %d partitions" % len(partition_set))

            partitions = []
            for partition in partition_set:
                part = Partition(partition)
                print((partition.path, part.size, part.raw_size))
                # skip ranges <5MB
                if part.raw_size > 5242880:
                    partitions.append(part)
                else:
                    print(("skipping ", partition.path, part.raw_size))
            partitions = sorted(partitions, key=lambda part: part.partition.geometry.start)

            print("      - Found partitions...")
            try: # assign mount_as and format_as if disk was just auto-formatted
                for partition, (mount_as, format_as) in zip(partitions, assign_mount_format):
                    partition.mount_as = mount_as
                    partition.format_as = format_as
                del assign_mount_format
            except NameError: pass
            print("      - Iterating partitions...")
            # Needed to fix the 1% minimum Partition.size_percent
            sum_size_percent = sum(p.size_percent for p in partitions) + .5  # .5 for good measure
            for partition in partitions:
                print("        . Appending partition %s..." % partition.name)
                partition.size_percent = round(partition.size_percent / sum_size_percent * 100, 1)
                installer.setup.partitions.append(partition)
                self.append(disk_iter, (partition.name,
                                        '<span foreground="{}">{}</span>'.format(partition.color, partition.type),
                                        partition.description,
                                        partition.format_as,
                                        partition.mount_as,
                                        partition.size,
                                        partition.free_space,
                                        partition,
                                        disk_path))

            print("      - Loading HTML view...")
            self.html_disks[disk_path] = DISK_TEMPLATE.format(PARTITIONS_HTML=''.join(PARTITION_TEMPLATE.format(p) for p in partitions))

    def get_html(self, disk):
        if disk in self.html_disks:
            return self.html_disks[disk]
        else:
            return ""

    def full_disk_format(self, device, is_backup):
        # Create a default partition set up
        disk_label = ('gpt' if device.getLength('B') > 2**32*.9 * device.sectorSize  # size of disk > ~2TB
                               or installer.setup.gptonefi
                            else 'msdos')
        if is_backup: separate_home_partition = (device.getLength('GB')*0.80 > 61)
        else: separate_home_partition = (device.getLength('GB') > 61)

        mkpart = (
            # (condition, mount_as, format_as, mkfs command, size_mb)
            # EFI
            (installer.setup.gptonefi, EFI_MOUNT_POINT, 'vfat', 'mkfs.vfat {} -F 32 ', 300),
            # swap - equal to RAM for hibernate to work well (but capped at ~8GB)
            (True, SWAP_MOUNT_POINT, 'swap', 'mkswap {}', min(8800, int(round(1.1/1024 * int(getoutput("awk '/^MemTotal/{ print $2 }' /proc/meminfo")), -2)))),
            # root
            (True, '/', 'ext4', 'mkfs.ext4 -F {}', 30000 if separate_home_partition else (1 if is_backup else 0)),
            # home
            (separate_home_partition, '/home', 'ext4', 'mkfs.ext4 -F {}', 1 if is_backup else 0),
            #BACKUP
            (is_backup, '', 'ext4', 'mkfs.ext4 -F {}', 0),
        )
        run_parted = lambda cmd: os.system('parted --script --align optimal {} {} ; sync'.format(device.path, cmd))
        run_parted('mklabel ' + disk_label)
        start_mb = 2
        partition_number = 0
        for partition in mkpart:
            if partition[0]:
                partition_number = partition_number + 1
                mkfs = partition[3]
                size_mb = partition[4]
                if size_mb == 1: end = '78%'
                elif size_mb: end = '{}MB'.format(start_mb + size_mb)
                else:
                    end = '100%'
                mkpart_cmd = 'mkpart primary {}MB {}'.format(start_mb, end)
                print(mkpart_cmd)
                run_parted(mkpart_cmd)
                mkfs = mkfs.format("%s%d" % (device.path, partition_number))
                print(mkfs)
                os.system(mkfs)
                if is_backup and end == '100%':
                    os.system ("tune2fs -L GRM_BACKUP %s%d" % (device.path, partition_number))
                start_mb += size_mb + 1
                if end == '78%': start_mb = device.getLength('MB')*0.8
        if installer.setup.gptonefi:
            run_parted('set 1 boot on')
        return ((i[1], i[2]) for i in mkpart if i[0])


def to_human_readable(size):
    for unit in [' ', _('kB'), _('MB'), _('GB'), _('TB'), 'PB', 'EB', 'ZB', 'YB']:
        if size < 1000:
            return "{:.1f} {}".format(size, unit)
        size /= 1000

class Partition(object):
    format_as = ''
    mount_as = ''

    def __init__(self, partition):
        assert partition.type not in (parted.PARTITION_METADATA, parted.PARTITION_EXTENDED)
        self.path = str(partition.path)

        print("              -> Building partition object for %s" % self.path)

        self.partition = partition
        self.length = partition.getLength()
        print("                  . length %d" % self.length)

        self.size_percent = max(1, round(80*self.length/partition.disk.device.getLength(), 1))
        print("                  . size_percent %d" % self.size_percent)

        self.size = to_human_readable(partition.getLength('B'))
        self.raw_size = partition.getLength('B')
        print("                  . size %s" % self.size)

        # if not normal partition with /dev/sdXN path, set its name to '' and discard it from model
        self.name = self.path if partition.number != -1 else ''
        print("                  . name %s" % self.name)

        try:
            self.type = partition.fileSystem.type
            for fs in ('swap', 'hfs', 'ufs'):  # normalize fs variations (parted.filesystem.fileSystemType.keys())
                if fs in self.type:
                    self.type = fs
            self.style = self.type
            print("                  . type %s" % self.type)
        except AttributeError:  # non-formatted partitions
            self.type = {
                parted.PARTITION_LVM: 'LVM',
                parted.PARTITION_SWAP: 'swap',
                parted.PARTITION_RAID: 'RAID',  # Empty space on Extended partition is recognized as this
                parted.PARTITION_PALO: 'PALO',
                parted.PARTITION_PREP: 'PReP',
                parted.PARTITION_LOGICAL: _('Logical partition'),
                parted.PARTITION_EXTENDED: _('Extended partition'),
                parted.PARTITION_FREESPACE: _('Free space'),
                parted.PARTITION_HPSERVICE: 'HP Service',
                parted.PARTITION_MSFT_RESERVED: 'MSFT Reserved',
            }.get(partition.type, _('Unknown'))
            self.style = {
                parted.PARTITION_SWAP: 'swap',
                parted.PARTITION_FREESPACE: 'freespace',
            }.get(partition.type, '')
            print("                  . type %s" % self.type)

        if "swap" in self.type:
            self.mount_as = SWAP_MOUNT_POINT

        # identify partition's description and used space
        try:
            print("                  . About to mount it...")
            os.system('mount --read-only {} {}'.format(self.path, TMP_MOUNTPOINT))
            size, free, self.used_percent, mount_point = getoutput("df {0} | grep '^{0}' | awk '{{print $2,$4,$5,$6}}' | tail -1".format(self.path)).split(None, 3)
            self.raw_size = int(size)*1024
            print("                  . size %s, free %s, self.used_percent %s, mount_point %s" % (size, free, self.used_percent, mount_point))
        except ValueError:
            print("                  . value error!")
            if "swap" in self.type:
                self.os_fs_info, self.description, self.free_space, self.used_percent = ': '+self.type, 'swap', '', 0
            else:
                print('WARNING: Partition {} or type {} failed to mount!'.format(self.path, partition.type))
                self.os_fs_info, self.description, self.free_space, self.used_percent = ': '+self.type, '', '', 0
            print("                  . self.os_fs_info %s, self.description %s, self.free_space %s, self.used_percent %s" % (self.os_fs_info, self.description, self.free_space, self.used_percent))
        else:
            print("                  . About to find more about it...")
            self.size = to_human_readable(int(size)*1024)  # for mountable partitions, more accurate than the getLength size above
            self.free_space = to_human_readable(int(free)*1024)  # df returns values in 1024B-blocks by default
            self.used_percent = self.used_percent.strip('%') or 0
            description = ''
            if path_exists(mount_point, 'etc/gooroom/info'):
                description = getoutput("cat %s/etc/gooroom/info | grep GRUB_TITLE" % mount_point).replace('GRUB_TITLE', '').replace('=', '').replace('"', '').strip()
            elif path_exists(mount_point, 'Windows/servicing/Version'):
                description = 'Windows ' + {
                    '6.4':'10',
                    '6.3':'8.1',
                    '6.2':'8',
                    '6.1':'7',
                    '6.0':'Vista',
                    '5.2':'XP Pro x64',
                    '5.1':'XP',
                    '5.0':'2000',
                    '4.9':'ME',
                    '4.1':'98',
                    '4.0':'95',
                }.get(getoutput('ls {}/Windows/servicing/Version'.format(mount_point))[:3], '')
            elif path_exists(mount_point, 'Boot/BCD'):
                description = 'Windows bootloader/recovery'
            elif path_exists(mount_point, 'Windows/System32'):
                description = 'Windows'
            elif path_exists(mount_point, 'System/Library/CoreServices/SystemVersion.plist'):
                description = 'Mac OS X'
            elif path_exists(mount_point, 'etc/'):
                description = getoutput("su -c '{{ . {0}/etc/lsb-release && echo $DISTRIB_DESCRIPTION; }} || \
                                                {{ . {0}/etc/os-release && echo $PRETTY_NAME; }}' gooroom".format(mount_point)) or 'Unix'
            else:
                try:
                    if partition.active:
                        for flag in partition.getFlagsAsString().split(", "):
                            if flag in ["boot", "esp"]:
                                description = 'EFI System Partition'
                                self.mount_as = EFI_MOUNT_POINT
                                break
                except Exception as detail:
                    # best effort
                    print("Could not read partition flags for %s: %s" % (self.path, detail))
            self.description = description
            self.os_fs_info = ': {0.description} ({0.type}; {0.size}; {0.free_space})'.format(self) if description else ': ' + self.type
            print("                  . self.description %s self.os_fs_info %s" % (self.description, self.os_fs_info))
        finally:
            print("                  . umounting it")
            os.system('umount ' + TMP_MOUNTPOINT + ' 2>/dev/null')
            print("                  . done")

        self.html_name = self.name.split('/')[-1]
        self.html_description = self.description
        if (self.size_percent < 10 and len(self.description) > 5):
            self.html_description = "%s..." % self.description[0:5]
        if (self.size_percent < 5):
            #Not enough space, don't write the name
            self.html_name = ""
            self.html_description = ""

        self.color = {
            # colors approximately from gparted (find matching set in usr/share/disk-partitions.html)
            'btrfs': '#636363',
            'exfat': '#47872a',
            'ext2':  '#2582a0',
            'ext3':  '#2582a0',
            'ext4':  '#21619e',
            'fat16': '#47872a',
            'fat32': '#47872a',
            'hfs':   '#636363',
            'jfs':   '#636363',
            'swap':  '#be3a37',
            'ntfs':  '#66a6a8',
            'reiserfs': '#636363',
            'ufs':   '#636363',
            'xfs':   '#636363',
            'zfs':   '#636363',
            parted.PARTITION_EXTENDED: '#a9a9a9',
        }.get(self.type, '#a9a9a9')

    def print_partition(self):
        print("Device: %s, format as: %s, mount as: %s" % (self.path, self.format_as, self.mount_as))


class PartitionDialog(object):
    def __init__(self, path, mount_as, format_as, type):
        glade_file = RESOURCE_DIR + 'interface.ui'
        self.builder = Gtk.Builder()
        self.builder.add_from_file(glade_file)
        self.window = self.builder.get_object("dialog")
        self.window.set_title(_("Edit partition"))
        self.builder.get_object("label_partition").set_markup("<b>%s</b>" % _("Device:"))
        self.builder.get_object("label_partition_value").set_label(path)
        self.builder.get_object("label_use_as").set_markup(_("Format as:"))
        self.builder.get_object("label_mount_point").set_markup(_("Mount point:"))
        self.window.vbox.get_children()[1].get_children()[0].get_children()[0].set_label(_("Cancel"))
        self.window.vbox.get_children()[1].get_children()[0].get_children()[1].set_label(_("Ok"))
        # Build supported filesystems list
        filesystems = ['', 'swap']
        for path in ["/bin", "/sbin"]:
            for fs in getoutput('echo %s/mkfs.*' % path).split():
                filesystems.append(fs.split("mkfs.")[1])
        filesystems = sorted(filesystems)
        filesystems = sorted(filesystems, key=lambda x: 0 if x in ('', 'ext4') else 1 if x == 'swap' else 2)
        model = Gtk.ListStore(str)
        for i in filesystems:
            model.append([i])
        self.builder.get_object("combobox_use_as").set_model(model)
        self.builder.get_object("combobox_use_as").set_active(filesystems.index(format_as))
        # Build list of pre-provided mountpoints
        combobox = self.builder.get_object("comboboxentry_mount_point")
        model = Gtk.ListStore(str, str)
        for i in ["/", "/home", "/boot", "/boot/efi", "/srv", "/tmp", "swap"]:
            model.append(["", i])
        combobox.set_model(model)
        combobox.set_entry_text_column(1)
        combobox.set_id_column(1)
        combobox.get_child().set_text(mount_as)

    def show(self):
        response = self.window.run()
        w = self.builder.get_object("comboboxentry_mount_point")
        mount_as = w.get_child().get_text().strip()
        w = self.builder.get_object("combobox_use_as")
        format_as = w.get_model()[w.get_active()][0]
        self.window.destroy()
        if response in (Gtk.ResponseType.YES, Gtk.ResponseType.APPLY, Gtk.ResponseType.OK, Gtk.ResponseType.ACCEPT):
            response_is_ok = True
        else:
            response_is_ok = False
        return response_is_ok, mount_as, format_as


class QuestionDialogWithCheckbox(Gtk.Dialog):
    import sys

    def __init__(self, title, message, parent=None):
        super().__init__(title=title, parent=parent, flags=0)

        self.set_default_size(600, 200)
        self.get_content_area().set_margin_start (20)
        self.get_content_area().set_margin_end (20)
        self.get_content_area().set_margin_bottom (20)
        self.get_content_area().set_margin_top (20)

        label = Gtk.Label(label=message)
        label.set_line_wrap (True)
        label.set_margin_bottom (35)
        self.get_content_area().add(label)

        self.checkbox = Gtk.CheckButton(label=_("Creating partitions for system terminal backup."))
        self.checkbox.set_active (True)
        self.get_content_area().add(self.checkbox)

        self.add_button(Gtk.STOCK_YES, Gtk.ResponseType.YES)
        self.add_button(Gtk.STOCK_NO, Gtk.ResponseType.NO)

        self.show_all()
