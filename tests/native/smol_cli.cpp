#include "smol_engine.h"
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

int main(int argc, char ** argv) {
    if (argc < 4 || argc > 7) {
        std::cerr << "Usage: cyber-smol-cli MODEL.gguf MMPROJ.gguf IMAGE [PROMPT] [MAX_TOKENS] [CANCEL_AFTER_MS]\n";
        return 2;
    }
    try {
        cyber_smol::load(argv[1], argv[2], 4);
        const std::string prompt = argc >= 5 ? argv[4] : "Describe only the visible object and its surface in one short sentence. Do not infer hidden faults.";
        const int tokens = argc >= 6 ? std::atoi(argv[5]) : 48;
        std::thread cancellation;
        if (argc >= 7) cancellation = std::thread([delay = std::atoi(argv[6])] {
            std::this_thread::sleep_for(std::chrono::milliseconds(delay));
            cyber_smol::cancel();
        });
        try {
            const auto result = cyber_smol::analyze(argv[3], prompt, tokens);
            if (cancellation.joinable()) cancellation.join();
            std::cout << result.json() << "\n";
        } catch (...) { if (cancellation.joinable()) cancellation.join(); throw; }
        cyber_smol::unload();
    } catch (const std::exception & error) {
        std::cerr << error.what() << "\n";
        return 1;
    }
}
