"""Give cloud CLI prompts a controlling terminal, without preexec hooks in threads."""
import os
import sys

if os.name != "nt":
    import fcntl
    import termios
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
os.execv(sys.argv[1], sys.argv[1:])
