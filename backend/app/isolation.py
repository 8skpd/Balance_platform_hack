"""Linux kernel policies applied before untrusted Python. No permissive fallback."""
import ctypes
import errno
import os
import platform
from pathlib import Path

libc = ctypes.CDLL(None, use_errno=True)

def check(result, action):
    if result < 0:
        raise OSError(ctypes.get_errno(), action)
    return result

def abi():
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'aarch64'):
        return 0
    return max(0, libc.syscall(444, 0, 0, 1))

def parent_death(parent_pid):
    check(libc.prctl(1, 9, 0, 0, 0), 'PR_SET_PDEATHSIG')
    if os.getppid() != parent_pid:
        os._exit(125)

def restrict_filesystem(read_paths):
    version = abi()
    if version < 3:
        raise RuntimeError('Для запуска Python требуется Linux Landlock ABI >= 3')
    class Ruleset(ctypes.Structure):
        _fields_ = [('fs', ctypes.c_uint64)]
    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]
    # Handle every filesystem permission through ABI 3, including REFER and TRUNCATE.
    rules = Ruleset((1 << 15) - 1)
    fd = check(libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0), 'landlock_create_ruleset')
    try:
        for value in set(str(Path(p).resolve()) for p in read_paths if Path(p).exists()):
            path = Path(value)
            access = 4 | (1 | 8 if path.is_dir() else 0)  # READ_FILE, EXECUTE, READ_DIR
            handle = os.open(value, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(access, handle)
                check(libc.syscall(445, fd, 1, ctypes.byref(rule), 0), 'landlock_add_rule')
            finally:
                os.close(handle)
        check(libc.prctl(38, 1, 0, 0, 0), 'PR_SET_NO_NEW_PRIVS')
        check(libc.syscall(446, fd, 0), 'landlock_restrict_self')
    finally:
        os.close(fd)

def restrict_syscalls():
    sec = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_syscall_resolve_name.restype = ctypes.c_int
    sec.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = sec.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not ctx:
        raise RuntimeError('Не удалось создать фильтр seccomp')
    deny = '''socket socketpair connect bind listen accept accept4 sendto sendmsg sendmmsg recvfrom recvmsg recvmmsg
        clone clone3 fork vfork execve execveat unshare setns ptrace process_vm_readv process_vm_writev
        pidfd_open pidfd_getfd pidfd_send_signal kill tkill tgkill bpf perf_event_open userfaultfd
        io_uring_setup io_uring_enter io_uring_register mount umount2 pivot_root chroot open_by_handle_at
        name_to_handle_at move_mount open_tree fsopen fsconfig fsmount fspick mount_setattr
        chmod fchmod fchmodat fchmodat2 chown fchown lchown fchownat utime utimes utimensat futimesat
        ioctl keyctl add_key request_key shmget shmat shmctl shmdt semget semop semtimedop semctl
        msgget msgsnd msgrcv msgctl mq_open mq_unlink mq_timedsend mq_timedreceive
        reboot kexec_load kexec_file_load init_module finit_module delete_module swapon swapoff
        acct quotactl settimeofday clock_settime adjtimex clock_adjtime syslog
        setxattr lsetxattr fsetxattr removexattr lremovexattr fremovexattr
        rt_sigqueueinfo rt_tgsigqueueinfo prlimit64 setpriority sched_setaffinity
        sched_setscheduler sched_setparam sched_setattr prctl
        pipe pipe2 fcntl fcntl64 fallocate
        setuid setreuid setresuid setfsuid setgid setregid setresgid setfsgid setgroups capset'''.split()
    try:
        for name in deny:
            syscall = sec.seccomp_syscall_resolve_name(name.encode())
            if syscall >= 0 and sec.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, syscall, 0) != 0:
                raise RuntimeError(f'Не удалось запретить syscall: {name}')
        # Synchronize with any existing library threads; fail if the filter cannot be applied.
        if sec.seccomp_attr_set(ctx, 4, 1) != 0 or sec.seccomp_load(ctx) != 0:
            raise RuntimeError('Не удалось применить seccomp')
    finally:
        sec.seccomp_release(ctx)

def apply(read_paths):
    restrict_filesystem(read_paths)
    restrict_syscalls()
