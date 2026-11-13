#pragma once
#include <cstdint>
#include <string>

namespace cyber_smol {
struct Result {
    std::string text;
    double prompt_ms = 0;
    double generate_ms = 0;
    double total_ms = 0;
    int generated_tokens = 0;
    int input_tokens = 0;
    bool cancelled = false;
    bool visual_findings = false;
    std::string json() const;
};
// load/analyze/unload serialize internally; cancel/is_loaded remain nonblocking.
std::uint64_t begin_operation();
void load(const std::string & model_path, const std::string & projector_path, int threads,
          std::uint64_t epoch = begin_operation());
Result analyze(const std::string & image_path, const std::string & prompt, int max_tokens,
               std::uint64_t epoch = begin_operation());
void cancel();
void unload();
bool is_loaded();
}
