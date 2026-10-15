# Machine probe fixtures

Every file here is the unedited output of a real command, made by
redirecting the command's output to the file. Do not edit them by hand.

## macOS

Captured on 2026-10-15 at 22:33:50 UTC.

Machine: MacBook Pro, Apple M4, 10 cores, 16 GiB, on AC power with the
battery at 94% and not charging.

OS: macOS 15.7.7 (build 24G720), Darwin 24.6.0, arm64. `pmset` and
`sysctl` have no version flag; they are the copies shipped with that
build, `/usr/bin/pmset` and `/usr/sbin/sysctl`.

| File | Command | Exit code |
|---|---|---|
| `pmset_g_batt.txt` | `pmset -g batt` | 0 |
| `pmset_g_therm.txt` | `pmset -g therm` | 0 |
| `pmset_g.txt` | `pmset -g` | 0 |
| `sysctl.txt` | `sysctl machdep.cpu.brand_string hw.physicalcpu hw.logicalcpu hw.memsize` | 0 |
| `sysctl_unknown_key.stdout.txt`, `sysctl_unknown_key.stderr.txt` | `sysctl machdep.cpu.brand_string kern.nope hw.memsize`, standard output and standard error | 1 |

## Linux arm64, in a container

Captured on 2026-10-15 starting 23:01:48 UTC, on the same Mac. Each
command ran in a fresh container:

```
docker run --rm --pull never --platform linux/arm64 python:3.12-slim <command>
```

- Docker Desktop 4.55.0, engine 29.1.3, with the VM given 10 CPUs.
- Image `python:3.12-slim`, arm64, digest
  `python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
- Kernel, from `uname -a` in the same kind of container
  (`linux_arm64_uname_a.txt`): `Linux 6.12.54-linuxkit #1 SMP Tue Nov 4
  21:21:47 UTC 2025 aarch64 GNU/Linux`.

This is Docker Desktop's LinuxKit VM, not bare metal. Several files
record that something is absent there; those absences are captured
results too.

| File | Command | Exit code |
|---|---|---|
| `linux_arm64_proc_cpuinfo.txt` | `cat /proc/cpuinfo` | 0 |
| `linux_arm64_uname_a.txt` | `uname -a` | 0 |
| `linux_arm64_boot_id_container_1.txt`, `linux_arm64_boot_id_container_2.txt` | `cat /proc/sys/kernel/random/boot_id`, in two separate containers | 0, 0 |
| `linux_arm64_etc_machine_id.stderr.txt` | `cat /etc/machine-id`, standard error; standard output was empty | 1 |
| `linux_arm64_dbus_machine_id.stderr.txt` | `cat /var/lib/dbus/machine-id`, standard error; standard output was empty | 1 |
| `linux_arm64_scaling_governor.stderr.txt` | `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`, standard error; standard output was empty | 1 |
| `linux_arm64_ls_sys_devices_system_cpu.txt` | `ls -1 /sys/devices/system/cpu` | 0 |
| `linux_arm64_ls_power_supply.txt` | `ls -1A /sys/class/power_supply`, standard output and error; the directory exists and is empty | 0 |
| `linux_arm64_ls_thermal.txt` | `ls -1A /sys/class/thermal`, standard output and error | 2 |

## Not captured

These have no captures. The tests of their parsers use samples
written inline in the test files from the documented formats, and say
so; they are not captures.

- Linux x86 `/proc/cpuinfo` and `nvidia-smi` output. Both need a Linux
  x86 or GPU host and are deferred to the GPU session (T5.12).
- Linux with a cpufreq governor, a battery or cooling devices.
- A valid machine-id.
- `pmset` on battery power or under thermal pressure.
