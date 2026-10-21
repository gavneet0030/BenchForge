#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <unordered_map>

int main(int argc, char* argv[]) {
    std::uint64_t iterations = 100000;
    std::uint64_t extra_work = 0;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];

        if (arg == "--iterations" && i + 1 < argc) {
            iterations = std::strtoull(argv[++i], nullptr, 10);
        } else if (arg == "--extra-work" && i + 1 < argc) {
            extra_work = std::strtoull(argv[++i], nullptr, 10);
        } else {
            std::cerr << "Unknown or incomplete argument: " << arg << '\n';
            return 2;
        }
    }

    if (iterations == 0) {
        std::cerr << "iterations must be greater than zero\n";
        return 2;
    }

    std::unordered_map<std::uint64_t, std::uint64_t> table;
    table.reserve(10000);

    for (std::uint64_t i = 0; i < 10000; ++i) {
        table.emplace(i, i * 17U + 3U);
    }

    // Setup is deliberately outside the timed region.
    std::uint64_t checksum = 0;
    volatile std::uint64_t sink = 0;

    const auto start = std::chrono::steady_clock::now();

    for (std::uint64_t i = 0; i < iterations; ++i) {
        const std::uint64_t key = i % 10000;
        const auto found = table.find(key);

        if (found != table.end()) {
            checksum += found->second;
        }

        // A controllable workload perturbation, not a calibrated slowdown percentage.
        for (std::uint64_t j = 0; j < extra_work; ++j) {
            checksum = (checksum * 33U) ^ (j + i);
        }
    }

    sink = checksum;
    const auto end = std::chrono::steady_clock::now();

    const auto elapsed_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();

    std::cout
        << "{\"schema_version\":1"
        << ",\"workload\":\"hash_lookup\""
        << ",\"iterations\":" << iterations
        << ",\"extra_work\":" << extra_work
        << ",\"elapsed_ns\":" << elapsed_ns
        << ",\"checksum\":" << static_cast<std::uint64_t>(sink)
        << "}\n";

    return 0;
}
