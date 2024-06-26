import os
import re
import subprocess
import time
import shutil
import gettext
import sys
import parted
import io

gettext.install("live-installer", "/usr/share/gooroom/locale")

CONFIG_FILE = '/etc/live-installer/live-installer.conf'

NON_LATIN_KB_LAYOUTS = ['am', 'af', 'ara', 'ben', 'bd', 'bg', 'bn', 'bt', 'by', 'deva', 'et', 'ge', 'gh', 'gn', 'gr', 'guj', 'guru', 'id', 'il', 'iku', 'in', 'iq', 'ir', 'kan', 'kg', 'kh', 'kz', 'la', 'lao', 'lk', 'ma', 'mk', 'mm', 'mn', 'mv', 'mal', 'my', 'np', 'ori', 'pk', 'ru', 'rs', 'scc', 'sy', 'syr', 'tel', 'th', 'tj', 'tam', 'tz', 'ua', 'uz']

class InstallerEngine:
    ''' This is central to the live installer '''

    def __init__(self):
        # Set distribution name and version
        def _get_config_dict(file, key_value=re.compile(r'^\s*(\w+)\s*=\s*["\']?(.*?)["\']?\s*(#.*)?$')):
            """Returns POSIX config file (key=value, no sections) as dict.
            Assumptions: no multiline values, no value contains '#'. """
            d = {}
            with open(file) as f:
                for line in f:
                    try: key, value, _ = key_value.match(line).groups()
                    except AttributeError: continue
                    d[key] = value
            return d
        for f, n, v in ((CONFIG_FILE, 'DISTRIBUTION_NAME', 'DISTRIBUTION_VERSION'),
                        ('/etc/os-release', 'PRETTY_NAME', 'VERSION'),
                        ('/etc/lsb-release', 'DISTRIB_DESCRIPTION', 'DISTRIB_RELEASE')):
            try:
                config = _get_config_dict(f)
                name, version = config[n], config[v]
            except (IOError, KeyError): continue
            else: break
        else: name, version = 'Unknown GNU/Linux', '1.0'
        self.distribution_name, self.distribution_version = name, version
        # Set other configuration
        config = _get_config_dict(CONFIG_FILE)
        self.live_user = config.get('live_user', 'user')
        self.media = config.get('live_media_source', '/run/live/medium/live/filesystem.squashfs')
        self.media_type = config.get('live_media_type', 'squashfs')
        # Flush print when it's called
        try:
            sys.stdout = io.TextIOWrapper(open(sys.stdout.fileno(), 'wb', 0), write_through=True)
        except TypeError:
            sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

    def set_progress_hook(self, progresshook):
        ''' Set a callback to be called on progress updates '''
        ''' i.e. def my_callback(progress_type, message, current_progress, total) '''
        ''' Where progress_type is any off PROGRESS_START, PROGRESS_UPDATE, PROGRESS_COMPLETE, PROGRESS_ERROR '''
        self.update_progress = progresshook

    def set_error_hook(self, errorhook):
        ''' Set a callback to be called on errors '''
        self.error_message = errorhook

    def get_distribution_name(self):
        return self.distribution_name

    def get_distribution_version(self):
        return self.distribution_version

    def step_format_partitions(self, setup):
        for partition in setup.partitions:
            if(partition.format_as is not None and partition.format_as != ""):
                # report it. should grab the total count of filesystems to be formatted ..
                self.update_progress(1, 4, True, False, _("Formatting %(partition)s as %(format)s ...") % {'partition':partition.path, 'format':partition.format_as})

                #Format it
                if partition.format_as == "swap":
                    cmd = "mkswap %s" % partition.path
                else:
                    if (partition.format_as in ['ext2', 'ext3', 'ext4']):
                        cmd = "mkfs.%s -F %s" % (partition.format_as, partition.path)
                    elif (partition.format_as == "jfs"):
                        cmd = "mkfs.%s -q %s" % (partition.format_as, partition.path)
                    elif (partition.format_as in ["btrfs", "xfs"]):
                        cmd = "mkfs.%s -f %s" % (partition.format_as, partition.path)
                    elif (partition.format_as == "vfat"):
                        cmd = "mkfs.%s %s -F 32" % (partition.format_as, partition.path)
                    else:
                        cmd = "mkfs.%s %s" % (partition.format_as, partition.path) # works with bfs, minix, msdos, ntfs, vfat

                self.do_unmount(partition.partition.path)
                print("EXECUTING: '%s'" % cmd)
                self.exec_cmd(cmd)
                partition.type = partition.format_as

            #
            # Assign LABEL
            #
            def init_label(label_name):
                cmd = "blkid | grep %s | awk -F':' '{print $1}'"% (label_name)
                p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, universal_newlines=True)
                for output in p.stdout.readlines():
                    print("tune2fs -L '' %s" % output)
                    os.system("tune2fs -L '' %s" % output)

            if(partition.mount_as == "/"):
                print("==== DEBUG ==== Assign GRM_ROOT_VOL label to the %s partition" % (partition.partition.path))
                init_label("GRM_ROOT_VOL")
                os.system("tune2fs -L GRM_ROOT_VOL " + partition.partition.path)

            if(partition.mount_as == "/recovery"):
                print("==== DEBUG ==== Assign GRM_RECOVERY label to the %s partition" % (partition.partition.path))
                init_label("GRM_RECOVERY")
                os.system("tune2fs -L GRM_RECOVERY " + partition.partition.path)

            if(partition.mount_as == "/boot/efi"):
                print("==== DEBUG ==== Assign GRM_BOOTEFI label to the %s partition" % (partition.partition.path))
                init_label("GRM_BOOTEFI")
                os.system("fatlabel %s GRM_BOOTEFI" % partition.partition.path)

    def step_mount_source(self, setup):
        # Mount the installation media
        print(" --> Mounting partitions")
        self.update_progress(2, 4, False, False, _("Mounting %(partition)s on %(mountpoint)s") % {'partition':self.media, 'mountpoint':"/source/"})
        print(" ------ Mounting %s on %s" % (self.media, "/source/"))
        self.do_mount(self.media, "/source/", self.media_type, options="loop")

    def step_mount_partitions(self, setup):
        self.step_mount_source(setup)

        # Mount the target partition
        for partition in setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != ""):
                  if partition.mount_as == "/":
                        self.update_progress(3, 4, False, False, _("Mounting %(partition)s on %(mountpoint)s") % {'partition':partition.path, 'mountpoint':"/target/"})
                        print(" ------ Mounting partition %s on %s" % (partition.path, "/target/"))
                        if partition.type == "fat32":
                            fs = "vfat"
                        else:
                            fs = partition.type
                        self.do_mount(partition.path, "/target", fs, None)
                        break

        # Mount the other partitions
        for partition in setup.partitions:
            if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                print(" ------ Mounting %s on %s" % (partition.path, "/target" + partition.mount_as))
                os.system("mkdir -p /target" + partition.mount_as)
                if partition.type == "fat16" or partition.type == "fat32":
                    fs = "vfat"
                else:
                    fs = partition.type
                self.do_mount(partition.path, "/target" + partition.mount_as, fs, None)

    def init_install(self, setup):
        # mount the media location.
        print(" --> Installation started")
        if(not os.path.exists("/target")):
            if (setup.skip_mount):
                self.error_message(message=_("ERROR: You must first manually mount your target filesystem(s) at /target to do a custom install!"))
                return
            os.mkdir("/target")
        if(not os.path.exists("/source")):
            os.mkdir("/source")
        # find the squashfs..
        if(not os.path.exists(self.media)):
            print("Base filesystem does not exist! Critical error (exiting).")
            self.error_message(message=_("ERROR: Something is wrong with the installation medium! This is usually caused by burning tools which are not compatible with LMDE (YUMI or other multiboot tools). Please burn the ISO image to DVD/USB using a different tool."))
            return

        os.system("umount --force /target/sys/firmware/efi/efivars")
        os.system("umount --force /target/dev/shm")
        os.system("umount --force /target/dev/pts")
        os.system("umount --force /target/dev/")
        os.system("umount --force /target/sys/")
        os.system("umount --force /target/proc/")
        os.system("umount --force /target/run/")
        os.system("umount --force /target/boot/efi")
        os.system("umount --force /target/recovery")

        if (not setup.skip_mount):
            self.step_format_partitions(setup)
            self.step_mount_partitions(setup)
        else:
            self.step_mount_source(setup)

        # Transfer the files
        SOURCE = "/source/"
        DEST = "/target/"
        EXCLUDE_DIRS = "home/* dev/* proc/* sys/* tmp/* run/* mnt/* media/* lost+found source target".split()
        our_current = 0
        # (Valid) assumption: num-of-files-to-copy ~= num-of-used-inodes-on-/
        our_total = int(subprocess.check_output("df --inodes /{src} | awk 'END{{ print $3 }}'".format(src=SOURCE.strip('/')), shell=True, universal_newlines=True))
        print(" --> Copying {} files".format(our_total))
        rsync_filter = ' '.join('--exclude=' + SOURCE + d for d in EXCLUDE_DIRS)
        rsync = subprocess.Popen("rsync --verbose --archive --no-D --acls "
                                 "--hard-links --xattrs {rsync_filter} "
                                 "{src}* {dst}".format(src=SOURCE, dst=DEST, rsync_filter=rsync_filter),
                                 shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        while rsync.poll() is None:
            line = rsync.stdout.readline()
            if not line:  # still copying the previous file, just wait
                time.sleep(0.1)
            else:
                our_current = min(our_current + 1, our_total)
                self.update_progress(our_current, our_total, False, False, _("Copying %s") % line.rstrip())
        print("rsync exited with returncode: " + str(rsync.poll()))

        # Steps:
        our_total = 11
        our_current = 0
        # chroot
        print(" --> Chrooting")
        self.update_progress(our_current, our_total, False, False, _("Entering the system ..."))
        os.system("mount --bind /dev/ /target/dev/")
        os.system("mount --bind /dev/shm /target/dev/shm")
        os.system("mount --bind /dev/pts /target/dev/pts")
        os.system("mount --bind /sys/ /target/sys/")
        os.system("mount --bind /proc/ /target/proc/")
        os.system("mount --bind /run/ /target/run/")
        os.system("mv /target/etc/resolv.conf /target/etc/resolv.conf.bk")
        os.system("cp -f /etc/resolv.conf /target/etc/resolv.conf")

        if os.path.exists("/sys/firmware/efi/efivars"):
            os.system("mkdir -p /target/sys/firmware/efi/efivars")
            os.system("mount --bind /sys/firmware/efi/efivars /target/sys/firmware/efi/efivars")

        kernelversion= subprocess.check_output("uname -r", shell=True, universal_newlines=True)
        os.system("cp /run/live/medium/live/vmlinuz /target/boot/vmlinuz-%s" % kernelversion)
        found_initrd = False
        for initrd in ["/run/live/medium/live/initrd.img", "/run/live/medium/live/initrd.lz"]:
            if os.path.exists(initrd):
                os.system("cp %s /target/boot/initrd.img-%s" % (initrd, kernelversion))
                found_initrd = True
                break

        if not found_initrd:
            print("WARNING: No initrd found!!")

        ############# for gooroom-initial-setup #############
        os.system("mkdir -p /target/debs")
        os.system("cp /run/live/medium/pool/main/g/gooroom-initial-setup/gooroom-initial-setup* /target/debs/")
        os.system("cp /run/live/medium/pool/main/g/gnome-online-accounts/* /target/debs/")
        self.do_run_in_chroot("dpkg -i /debs/*.deb")
        os.system("rm -rf /target/debs")
        #####################################################

        if (setup.gptonefi):
            print(" --> Installing EFI packages and Adding EFI entry")
            os.system("mkdir -p /target/boot/efi/EFI/debian")
            os.system("cp /run/live/medium/EFI/boot/grubx64.efi /target/boot/efi/EFI/debian")
            os.system("mkdir -p /target/debs")
            os.system("cp /run/live/medium/pool/main/g/grub2/grub-efi* /target/debs/")
            os.system("cp /run/live/medium/pool/main/e/efibootmgr/efibootmgr* /target/debs/")
            os.system("cp /run/live/medium/pool/main/e/efivar/* /target/debs/")
            os.system("cp /run/live/medium/pool/main/s/shim/* /target/debs/")
            self.do_run_in_chroot("DEBIAN_FRONTEND=noninteractive dpkg -P grub-pc grub-pc-bin")
            self.do_run_in_chroot("dpkg -i /debs/*.deb")

            #if(not os.path.exists("/target/boot/efi/EFI/gooroom/grubx64.efi")):
                #
                # TODO : Check signed grubx64.efi file
                #        - dell OptiPlex 7040
                #        - target=x86_64-efi
                #
                #/partition.partition.path
                #for partition in setup.partitions:
                #    if (partition.format_as == "vfat"):
                #        print "--> partition.partition.path => \"%s\"" % partition.partition.path
                #        self.do_run_in_chroot("efibootmgr --create --disk /dev/sda --part 1 -w --label gooroom --loader '\EFI\gooroom\grubx64.efi'")
                #        self.do_run_in_chroot("efibootmgr -c -d /dev/sda -p 1 -L 'Gooroom' -l '\EFI\gooroom\grubx64.efi'")
            self.do_run_in_chroot("grub-install --bootloader-id=gooroom --removable")

            os.system("rm -rf /target/debs")

        # Detect cdrom device
        # TODO : properly detect cdrom device
        # Mount it
        # os.system("mkdir -p /target/media/cdrom")
        # if (int(os.system("mount /dev/sr0 /target/media/cdrom"))):
        #     print " --> Failed to mount CDROM. Install will fail"
        # self.do_run_in_chroot("apt-cdrom -o Acquire::cdrom::AutoDetect=false -m add")

        # remove live-packages (or w/e)
        print(" --> Removing live packages")
        our_current += 1
        self.update_progress(our_current, our_total, False, False, _("Removing live configuration (packages)"))
        with open("/run/live/medium/live/filesystem.packages-remove", "r") as fd:
            line = fd.read().replace('\n', ' ')
        self.do_run_in_chroot("apt remove --purge --yes %s" % line)

        # remove live leftovers
        self.do_run_in_chroot("rm -rf /etc/live")
        self.do_run_in_chroot("rm -rf /run/live")

        # Lock and delete root password
        self.do_run_in_chroot("passwd -dl root")

        # Set LightDM to show user list by default
        self.do_run_in_chroot(r"sed -i -r 's/^#?(greeter-hide-users)\s*=.*/\1=false/' /etc/lightdm/lightdm.conf")

        # write the /etc/fstab
        print(" --> Writing fstab")
        our_current += 1
        self.update_progress(our_current, our_total, False, False, _("Writing filesystem mount information to /etc/fstab"))
        # make sure fstab has default /proc and /sys entries
        #
        # A bug in adding partition table to the fstab if the partition was mounted automatically
        #
        #if(not os.path.exists("/target/etc/fstab")):
        os.system("echo \"#### Static Filesystem Table File\" > /target/etc/fstab")
        fstab = open("/target/etc/fstab", "a")
        fstab.write("proc\t/proc\tproc\tdefaults\t0\t0\n")
        if(not setup.skip_mount):
            for partition in setup.partitions:
                if (partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "None"):
                    partition_uuid = partition.path # If we can't find the UUID we use the path
                    blkid = subprocess.check_output('blkid', shell=True, universal_newlines=True).split('\n')
                    for blkid_line in blkid:
                        blkid_elements = blkid_line.split(':')
                        if blkid_elements[0] == partition.path:
                            blkid_mini_elements = blkid_line.split()
                            for blkid_mini_element in blkid_mini_elements:
                                if "UUID=" in blkid_mini_element:
                                    partition_uuid = blkid_mini_element.replace('"', '').strip()
                                    break
                            break

                    fstab.write("# %s\n" % (partition.path))

                    if(partition.mount_as == "/"):
                        fstab_fsck_option = "1"
                    else:
                        fstab_fsck_option = "0"

                    if("ext" in partition.type):
                        fstab_mount_options = "rw,errors=remount-ro"
                    else:
                        fstab_mount_options = "defaults"

                    if partition.type == "fat16" or partition.type == "fat32":
                        fs = "vfat"
                    else:
                        fs = partition.type

                    if(fs == "swap"):
                        fstab.write("%s\tswap\tswap\tsw\t0\t0\n" % partition_uuid)
                    else:
                        fstab.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (partition_uuid, partition.mount_as, fs, fstab_mount_options, "0", fstab_fsck_option))
        fstab.close()

    def do_archive_partition(self, our_total, our_current, setup):
        archive_root_partition = None
        archive_bootefi_partition = None
        archive_recovery_partition = None

        for partition in setup.partitions:
            if(partition.mount_as == "/"):
                print("==== DEBUG ==== Assign archive_root_partition to the %s partition" % (partition.partition.path))
                archive_root_partition = partition.partition.path

            if(partition.mount_as == "/boot/efi"):
                print("==== DEBUG ==== Assign archive_boot_partition to the %s partition" % (partition.partition.path))
                archive_bootefi_partition = partition.partition.path

            if(partition.mount_as == "/recovery"):
                print("==== DEBUG ==== Assign archive_recovery_partition to the %s partition" % (partition.partition.path))
                archive_recovery_partition = partition.partition.path

                print(" --> Supporting Gooroom RECOVERY Mode")
                self.update_progress(our_current, our_total, False, False, _("Configuring Recovery Mode"))

                self.do_mount(archive_recovery_partition, "/target", "ext4", None)
            #os.system("mount %s /target/recovery" % archive_recovery_partition)

                print("fsarchiver savefs /target/gooroom_root_partition.fsa %s" % (archive_root_partition))
                os.system("fsarchiver savefs /target/gooroom_root_partition.fsa %s" % archive_root_partition)

                if archive_bootefi_partition is not None:
                    print("fsarchiver savefs /target/gooroom_bootefi_partition.fsa %s" % (archive_bootefi_partition))
                    os.system("fsarchiver savefs /target/gooroom_bootefi_partition.fsa %s" % archive_bootefi_partition)

                os.system("umount --force %s" % archive_recovery_partition)

    def finish_install(self, setup):
        # Steps:
        our_total = 12
        our_current = 5

        # write host+hostname infos
        print(" --> Writing hostname")
        our_current += 1
        self.update_progress(our_current, our_total, False, False, _("Setting hostname"))
        hostnamefh = open("/target/etc/hostname", "w")
        hostnamefh.write("%s\n" % setup.hostname)
        hostnamefh.close()
        hostsfh = open("/target/etc/hosts", "w")
        hostsfh.write("127.0.0.1\tlocalhost\n")
        hostsfh.write("127.0.1.1\t%s\n" % setup.hostname)
        hostsfh.write("# The following lines are desirable for IPv6 capable hosts\n")
        hostsfh.write("::1     localhost ip6-localhost ip6-loopback\n")
        hostsfh.write("fe00::0 ip6-localnet\n")
        hostsfh.write("ff00::0 ip6-mcastprefix\n")
        hostsfh.write("ff02::1 ip6-allnodes\n")
        hostsfh.write("ff02::2 ip6-allrouters\n")
        hostsfh.write("ff02::3 ip6-allhosts\n")
        hostsfh.close()

        # set the locale
        print(" --> Setting the locale")
        our_current += 1
        self.update_progress(our_current, our_total, False, False, _("Setting locale"))
        os.system("echo \"%s.UTF-8 UTF-8\" >> /target/etc/locale.gen" % setup.language)
        self.do_run_in_chroot("locale-gen")
        os.system("echo \"\" > /target/etc/default/locale")
        self.do_run_in_chroot("update-locale LANG=\"%s.UTF-8\"" % setup.language)
        self.do_run_in_chroot("update-locale LANG=%s.UTF-8" % setup.language)

        # set the timezone
        print(" --> Setting the timezone")
        os.system("echo \"%s\" > /target/etc/timezone" % setup.timezone)
        os.system("rm -f /target/etc/localtime")
        os.system("ln -s /usr/share/zoneinfo/%s /target/etc/localtime" % setup.timezone)

        if os.path.exists("/etc/gooroom/info"):
            # NT500R3W-LD2A, NT340XAA-K201G
            self.do_run_firmware_atheros("NT500R3W")
            self.do_run_firmware_atheros("NT340XAA")

            # NT500R3W-LD2A, NT340XAA-K201G
            self.do_run_firmware_atheros("NT500R3W")
            self.do_run_firmware_atheros("NT340XAA")

        # generate the iso info
        print(" --> Generating /target/etc/gooroom/.isoinfo")
        os.system("echo \"#\n# DO NOT EDIT THIS FILE\n# It is automatically generated by live-installer\n#\" > /target/etc/gooroom/.isoinfo")
        os.system("cat /lib/live/mount/medium/.disk/info >> /target/etc/gooroom/.isoinfo")

        # set the keyboard options..
        print(" --> Setting the keyboard")
        our_current += 1
        self.update_progress(our_current, our_total, False, False, _("Setting keyboard options"))
        consolefh = open("/target/etc/default/console-setup", "r")
        newconsolefh = open("/target/etc/default/console-setup.new", "w")
        for line in consolefh:
            line = line.rstrip("\r\n")
            if(line.startswith("XKBMODEL=")):
                newconsolefh.write("XKBMODEL=\"%s\"\n" % setup.keyboard_model)
            elif(line.startswith("XKBLAYOUT=")):
                newconsolefh.write("XKBLAYOUT=\"%s\"\n" % setup.keyboard_layout)
            elif(line.startswith("XKBVARIANT=") and setup.keyboard_variant is not None and setup.keyboard_variant != ""):
                newconsolefh.write("XKBVARIANT=\"%s\"\n" % setup.keyboard_variant)
            else:
                newconsolefh.write("%s\n" % line)
        consolefh.close()
        newconsolefh.close()
        self.do_run_in_chroot("rm /etc/default/console-setup")
        self.do_run_in_chroot("mv /etc/default/console-setup.new /etc/default/console-setup")

        consolefh = open("/target/etc/default/keyboard", "r")
        newconsolefh = open("/target/etc/default/keyboard.new", "w")
        for line in consolefh:
            line = line.rstrip("\r\n")
            if(line.startswith("XKBMODEL=")):
                newconsolefh.write("XKBMODEL=\"%s\"\n" % setup.keyboard_model)
            elif(line.startswith("XKBLAYOUT=")):
                newconsolefh.write("XKBLAYOUT=\"%s\"\n" % setup.keyboard_layout)
            elif(line.startswith("XKBVARIANT=") and setup.keyboard_variant is not None and setup.keyboard_variant != ""):
                newconsolefh.write("XKBVARIANT=\"%s\"\n" % setup.keyboard_variant)
            elif(line.startswith("XKBOPTIONS=")):
                newconsolefh.write("XKBOPTIONS=grp:ctrls_toggle")
            else:
                newconsolefh.write("%s\n" % line)
        consolefh.close()
        newconsolefh.close()
        self.do_run_in_chroot("rm /etc/default/keyboard")
        self.do_run_in_chroot("mv /etc/default/keyboard.new /etc/default/keyboard")

        # Perform OS adjustments (this is needed prior to installing grub)
        if os.path.exists("/target/usr/lib/gooroom/gooroomSystem/gooroom-adjust.py"):
            self.do_run_in_chroot("/usr/lib/gooroom/gooroomSystem/gooroom-adjust.py")

        # write MBR (grub)
        print(" --> Configuring Grub")
        our_current += 1
        if(setup.grub_device is not None):
            self.update_progress(our_current, our_total, False, False, _("Installing bootloader"))
            print(" --> Running grub-install")
            self.do_run_in_chroot("grub-install --force %s --recheck" % setup.grub_device)
            #fix not add windows grub entry
            self.do_run_in_chroot("update-grub")
            self.do_configure_grub(our_total, our_current)
            grub_retries = 0
            while (not self.do_check_grub(our_total, our_current)):
                self.do_configure_grub(our_total, our_current)
                grub_retries = grub_retries + 1
                if grub_retries >= 5:
                    self.error_message(message=_("WARNING: The grub bootloader was not configured properly! You need to configure it manually."))
                    break

        # Recovery Mode : Install packages
        for partition in setup.partitions:
            if(partition.mount_as == "/recovery"):
                print(" --> Configuring Recovery Mode")
                os.system("mkdir -p /target/debs")

                # UEFI boot
                if(partition.mount_as == "/boot/efi"):
                    # install recovery pkgs
                    os.system("cp /run/live/medium/pool/main/f/fsarchiver/*.deb /target/debs/")
                    os.system("cp /run/live/medium/pool/main/g/gooroom-recovery-utils/*.deb /target/debs/")

                    # remove grub pkgs
                    self.do_run_in_chroot("apt remove --purge --yes grub-common grub-efi-amd64 grub-efi-amd64-bin grub2-common")

                # Legacy boot
                else:
                    # install recovery pkgs
                    os.system("cp /run/live/medium/pool/main/f/fsarchiver/*.deb /target/debs/")
                    os.system("cp /run/live/medium/pool/main/g/gooroom-recovery-utils/*.deb /target/debs/")
                    os.system("cp /run/live/medium/pool/main/e/efibootmgr/efibootmgr* /target/debs/")
                    os.system("cp /run/live/medium/pool/main/e/efivar/* /target/debs/")

                    # remove grub pkgs
                    self.do_run_in_chroot("DEBIAN_FRONTEND=noninteractive apt -o \"Dpkg::Option::=--force-confold\" purge -y grub-common grub-efi-amd64 grub-efi-amd64-bin grub2-common")

                # install gooroom-grub pkgs
                os.system("cp /run/live/medium/pool/main/g/gooroom-grub/*.deb /target/debs/")

                # install pkgs
                self.do_run_in_chroot("dpkg -i /debs/*.deb")
                os.system("rm -rf /target/debs")

        # IMA Mode
        if os.path.exists("/run/live/medium/pool/main/g/gooroom-exe-protector"):
            print(" --> IMA : installing gooroom-exe-protector")
            os.system("mkdir -p /target/debs")
            os.system("cp /run/live/medium/pool/main/g/gooroom-exe-protector/*.deb /target/debs/")
            self.do_run_in_chroot("dpkg -i /debs/*.deb")
            os.system("rm -rf /target/debs")

            print(" --> Writing file signatures")
            our_current += 1
            self.update_progress(our_total, our_current, False, False, _("Writing file signatures"))
            os.system("chroot /target/ /bin/bash -c \"/ima/setsigs.sh /ima\"")
            os.system("rm -rf /target/ima")
        else:
            # Recreate initramfs (needed in case of skip_mount also, to include things like mdadm/dm-crypt/etc in case its needed to boot a custom install)
            print(" --> Configuring Initramfs")
            our_current += 1
            self.do_run_in_chroot("/usr/sbin/update-initramfs -t -u -k all")
            kernelversion= subprocess.check_output("uname -r", shell=True, universal_newlines=True)
            self.do_run_in_chroot("/usr/bin/sha1sum /boot/initrd.img-%s > /var/lib/initramfs-tools/%s" % (kernelversion,kernelversion))

        # Recovery Mode : Copy vmlinuz and initrd.img to recovery directory
        for partition in setup.partitions:
            if(partition.mount_as == "/recovery"):
                os.system("mkdir -p /target/recovery/boot")
                os.system("cp -f /target/boot/vmlinuz-* /target/recovery/boot")
                os.system("cp -f /target/boot/initrd.img-* /target/recovery/boot")

        # Clean APT
        print(" --> Cleaning APT")
        our_current += 1
        self.update_progress(our_current, our_total, True, False, _("Cleaning APT"))
        os.system("chroot /target/ /bin/sh -c \"dpkg --configure -a\"")
        self.do_run_in_chroot("sed -i 's/^deb cdrom/#deb cdrom/' /etc/apt/sources.list")
        self.do_run_in_chroot("apt --yes autoremove")

        # now unmount it
        print(" --> Unmounting partitions")

        if os.path.exists("/target/sys/firmware/efi/efivars"):
            os.system("umount --force /target/sys/firmware/efi/efivars")

        os.system("umount --force /target/dev/shm")
        os.system("umount --force /target/dev/pts")
        if setup.gptonefi:
            os.system("umount --force /target/boot/efi")
            os.system("umount --force /target/media/cdrom")
        os.system("umount --force /target/dev/")
        os.system("umount --force /target/sys/fs/fuse/connections")
        os.system("umount --force /target/sys/")
        os.system("umount --force /target/proc/")
        os.system("umount --force /target/run/")
        os.system("rm -f /target/etc/resolv.conf")
        os.system("mv /target/etc/resolv.conf.bk /target/etc/resolv.conf")
        if(not setup.skip_mount):
            for partition in setup.partitions:
                if(partition.mount_as is not None and partition.mount_as != "" and partition.mount_as != "/" and partition.mount_as != "swap"):
                    self.do_unmount("/target" + partition.mount_as)

            # btrfs subvolumes are mounts, but will block unmounting /target. This will
            # unmount the submounts also.
            cmd = "umount -AR /target"
            print("Unmounting the target root: '%s'" % cmd)
            self.exec_cmd(cmd)
        self.do_unmount("/source")

        ## Recovery Mode
        self.do_archive_partition(our_total, our_current, setup)

        self.update_progress(0, 0, False, True, _("Installation finished"))
        print(" --> All done")

    def do_run_firmware_atheros(self, device):
        try :
            subprocess.check_output('dmidecode | grep -q %s' %device, shell=True, universal_newlines=True)
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                print("Found %s" %device)
                os.system("mkdir -p /target/debs")
                os.system("cp /lib/live/mount/medium/pool/main/f/firmware-nonfree/firmware-atheros*.deb /target/debs/")
                self.do_run_in_chroot("dpkg -i /debs/*")
            else:
                print("Not found %s" %device)

    def do_run_in_chroot(self, command):
        command = command.replace('"', "'").strip()
        print("chroot /target/ /bin/sh -c \"%s\"" % command)
        os.system("chroot /target/ /bin/sh -c \"%s\"" % command)

    def do_configure_grub(self, our_total, our_current):
        self.update_progress(our_current, our_total, True, False, _("Configuring bootloader"))
        #if not setup.gptonefi:
        print(" --> Running grub-mkconfig")
        self.do_run_in_chroot("grub-mkconfig -o /boot/grub/grub.cfg")
        grub_output = subprocess.check_output("chroot /target/ /bin/sh -c \"grub-mkconfig -o /boot/grub/grub.cfg\"", shell=True, universal_newlines=True)
        grubfh = open("/var/log/live-installer-grub-output.log", "w")
        grubfh.writelines(grub_output)
        grubfh.close()


    def do_check_grub(self, our_total, our_current):
        self.update_progress(our_current, our_total, True, False, _("Checking bootloader"))
        print(" --> Checking Grub configuration")
        time.sleep(5)
        found_entry = False
        if os.path.exists("/target/boot/grub/grub.cfg"):
            grubfh = open("/target/boot/grub/grub.cfg", "r")
            for line in grubfh:
                line = line.rstrip("\r\n")
                #if("06_gooroom_theme" in line):
                #    found_theme = True
                #    print " --> Found Grub theme: %s " % line
                if ("menuentry" in line and ("class gooroom" in line or "Gooroom" in line or "Debian" in line)):
                    found_entry = True
                    print(" --> Found Grub entry: %s " % line)
            grubfh.close()
            return (found_entry)
        else:
            print("!No /target/boot/grub/grub.cfg file found!")
            return False

    def do_mount(self, device, dest, type, options=None):
        ''' Mount a filesystem '''
        p = None
        if(options is not None):
            cmd = "mount -o %s -t %s %s %s" % (options, type, device, dest)
        else:
            cmd = "mount -t %s %s %s" % (type, device, dest)
        print("EXECUTING: '%s'" % cmd)
        self.exec_cmd(cmd)

    def do_unmount(self, mountpoint):
        ''' Unmount a filesystem '''
        cmd = "umount %s" % mountpoint
        print("EXECUTING: '%s'" % cmd)
        self.exec_cmd(cmd)

    # Execute schell command and return output in a list
    def exec_cmd(self, cmd):
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        lstOut = []
        for line in p.stdout.readlines():
            # Strip the line, also from null spaces (strip() only strips white spaces)
            line = line.strip().strip("\0")
            if line != '':
                lstOut.append(line)
        return lstOut

# Represents the choices made by the user
class Setup(object):
    language = None
    timezone = None
    keyboard_model = None
    keyboard_layout = None
    keyboard_variant = None
    partitions = [] #Array of PartitionSetup objects
    hostname = "gooroom"
    autologin = False
    password1 = None
    password2 = None
    grub_device = None
    ecryptfs = True
    encfs = False
    disks = []
    target_disk = None
    gptonefi = False
    # Optionally skip all mouting/partitioning for advanced users with custom setups (raid/dmcrypt/etc)
    # Make sure the user knows that they need to:
    #  * Mount their target directory structure at /target
    #  * NOT mount /target/dev, /target/dev/shm, /target/dev/pts, /target/proc, and /target/sys
    #  * Manually create /target/etc/fstab after init_install has completed and before finish_install is called
    #  * Install cryptsetup/dmraid/mdadm/etc in target environment (using chroot) between init_install and finish_install
    #  * Make sure target is mounted using the same block device as is used in /target/etc/fstab (eg if you change the name of a dm-crypt device between now and /target/etc/fstab, update-initramfs will likely fail)
    skip_mount = False

    #Descriptions (used by the summary screen)
    keyboard_model_description = None
    keyboard_layout_description = None
    keyboard_variant_description = None

    def print_setup(self):
        if __debug__:
            print("-------------------------------------------------------------------------")
            print("language: %s" % self.language)
            print("timezone: %s" % self.timezone)
            print("keyboard: %s - %s (%s) - %s - %s (%s)" % (self.keyboard_model, self.keyboard_layout, self.keyboard_variant, self.keyboard_model_description, self.keyboard_layout_description, self.keyboard_variant_description))
            print("autologin: ", self.autologin)
            print("hostname: %s " % self.hostname)
            print("passwords: %s - %s" % (self.password1, self.password2))
            print("grub_device: %s " % self.grub_device)
            print("ecryptfs: %s " % self.ecryptfs)
            print("encfs: %s " % self.encfs)
            print("skip_mount: %s" % self.skip_mount)
            if (not self.skip_mount):
                print("target_disk: %s " % self.target_disk)
                if self.gptonefi:
                    print("GPT partition table: True")
                else:
                    print("GPT partition table: False")
                print("disks: %s " % self.disks)
                print("partitions:")
                for partition in self.partitions:
                    partition.print_partition()
            print("-------------------------------------------------------------------------")
