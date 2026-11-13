#include "smol_engine.h"
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <thread>

void require(bool condition, const char * message) {
    if (!condition) throw std::runtime_error(message);
}

int main(int argc, char ** argv) {
    if (argc != 4) { std::cerr << "Usage: cyber-smol-smoke MODEL MMPROJ IMAGE\n"; return 2; }
    try {
        require(!cyber_smol::is_loaded(), "Model unexpectedly resident before load");
        cyber_smol::load(argv[1], argv[2], 4);
        require(cyber_smol::is_loaded(), "Model is not resident");
        bool rejected = false;
        try { cyber_smol::analyze(argv[3], "", 48); } catch (const std::exception &) { rejected = true; }
        require(rejected, "Empty prompt accepted");
        rejected = false;
        try { cyber_smol::analyze("/file-does-not-exist.jpg", "Describe the image.", 48); } catch (const std::exception &) { rejected = true; }
        require(rejected, "Missing image accepted");
        const auto stale = cyber_smol::begin_operation();
        cyber_smol::cancel();
        const auto skipped = cyber_smol::analyze(argv[3], "Describe the image.", 48, stale);
        require(skipped.cancelled && skipped.input_tokens == 0, "Pre-entry cancellation was lost");
        const auto active = cyber_smol::begin_operation();
        std::thread stopper([] {
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
            cyber_smol::cancel();
        });
        cyber_smol::Result stopped;
        try { stopped = cyber_smol::analyze(argv[3], "Describe the image.", 128, active); }
        catch (...) { stopper.join(); throw; }
        stopper.join();
        require(stopped.total_ms < 5000, "Vision encoding did not stop promptly");
        require(stopped.cancelled, "Cancellation was ignored");
        require(cyber_smol::is_loaded(), "Cancellation unexpectedly unloaded weights");
        auto complete = cyber_smol::analyze(argv[3], "What is the main object in this image? Describe its color and visible surface.", 48);
        require(!complete.cancelled && !complete.text.empty(), "Analysis failed after cancellation");
        require(complete.generated_tokens <= 48 && complete.input_tokens < 2048, "Token bounds exceeded");
        // A second frame uses the same resident weights, with cleared KV state.
        auto repeated = cyber_smol::analyze(argv[3], "What is the main object in this image? Describe its color and visible surface.", 48);
        require(repeated.text == complete.text, "Frame context leaked across repeated greedy analysis");
        cyber_smol::unload();
        require(!cyber_smol::is_loaded(), "Model remained resident after unload");
        cyber_smol::unload();
        std::cout << "{\"checks\":\"load, invalid inputs, cancel, recover, persistent weights, isolated context, unload\",\"result\":"
                  << complete.json() << ",\"repeat\":" << repeated.json() << "}\n";
    } catch (const std::exception & error) { std::cerr << error.what() << "\n"; return 1; }
}
