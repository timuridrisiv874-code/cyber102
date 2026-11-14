#ifndef CYBER_INSPECTION_H
#define CYBER_INSPECTION_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Capture the epoch before queuing a load or an inspection. Cancellation invalidates that epoch.
uint64_t cyber_lfm_begin(void);
// Returns NULL on success, otherwise an owned UTF-8 error string.
char *cyber_lfm_load(const char *model_path, const char *projector_path, int32_t threads, uint64_t epoch);
// Returns an owned UTF-8 JSON object with the observation and timings, or an "error" string.
char *cyber_lfm_analyze(const char *jpeg_path, const char *prompt, int32_t max_tokens, uint64_t epoch);
void cyber_lfm_free(char *result);
void cyber_lfm_cancel(void);
void cyber_lfm_unload(void);
bool cyber_lfm_loaded(void);

#ifdef __cplusplus
}
#endif
#endif
