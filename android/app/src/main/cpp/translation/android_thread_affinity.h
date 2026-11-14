#pragma once

#include <errno.h>
#include <pthread.h>
#include <sched.h>

// Bionic exposes the Linux affinity syscall and pthread-to-tid mapping separately.
static inline int pthread_setaffinity_np(pthread_t thread, size_t length, const cpu_set_t* mask) {
    const pid_t tid = pthread_gettid_np(thread);
    if (tid <= 0) return ESRCH;
    return sched_setaffinity(tid, length, mask) == 0 ? 0 : errno;
}
