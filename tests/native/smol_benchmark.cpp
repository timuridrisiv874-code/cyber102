#include "smol_engine.h"
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <sys/resource.h>

int main(int argc, char ** argv) {
    if (argc < 4 || argc > 8) { std::cerr << "Usage: cyber-smol-benchmark MODEL MMPROJ IMAGE... (max 5)\n"; return 2; }
    try {
        const char * custom_prompt = std::getenv("CYBER_SMOL_PROMPT");
        const char * custom_budget = std::getenv("CYBER_SMOL_MAX_TOKENS");
        const std::string prompt = custom_prompt ? custom_prompt : "What is the main object in this image? Describe its color and visible surface.";
        const int max_tokens = custom_budget ? std::stoi(custom_budget) : 48;
        if (max_tokens < 1 || max_tokens > 128) throw std::runtime_error("Token budget must be 1..128");
        const auto start = std::chrono::steady_clock::now();
        cyber_smol::load(argv[1], argv[2], 4);
        const auto loaded = std::chrono::steady_clock::now();
        std::cout << "{\"loadMs\":" << std::chrono::duration<double, std::milli>(loaded - start).count() << ",\"results\":[";
        for (int i = 3; i < argc; ++i) {
            if (i > 3) std::cout << ',';
            std::cout << cyber_smol::analyze(argv[i], prompt, max_tokens).json() << std::flush;
        }
        struct rusage usage{};
        getrusage(RUSAGE_SELF, &usage);
        long long peak_bytes = usage.ru_maxrss;
#ifndef __APPLE__
        peak_bytes *= 1024;
#endif
        std::cout << "],\"maxResidentBytes\":" << peak_bytes << "}\n";
        cyber_smol::unload();
    } catch (const std::exception & error) { std::cerr << error.what() << "\n"; return 1; }
}
