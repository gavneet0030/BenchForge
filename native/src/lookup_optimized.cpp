#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <unordered_map>

int main(int argc, char* argv[]) {
    std::uint64_t iterations = 100000;
    std::uint64_t extra_work = 0;
    std::uint64_t delay_ns = 0;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];

        if (arg == "--iterations" && i + 1 < argc) {
            iterations = std::strtoull(argv[++i], nullptr, 10);
        } else if (arg == "--extra-work" && i + 1 < argc) {
            extra_work = std::strtoull(argv[++i], nullptr, 10);
        } else if (arg == "--delay-ns" && i + 1 < argc) {
            delay_ns = std::strtoull(argv[++i], nullptr, 10);
        } else {
            std::cerr << "Unknown or incomplete argument: " << arg << '\n';
            return 2;
        }
    }

    if (iterations == 0) {
        std::cerr << "iterations must be greater than zero\n";
        return 2;
    }

    constexpr std::uint64_t table_size = 10000;
    std::unordered_map<std::uint64_t, std::uint64_t> table;
    table.reserve(table_size);

    for (std::uint64_t i = 0; i < table_size; ++i) {
        table.emplace(i, i * 17U + 3U);
    }

    std::uint64_t checksum = 0;
    volatile std::uint64_t sink = 0;
    std::uint64_t key = 0;

    const auto start = std::chrono::steady_clock::now();

    for (std::uint64_t i = 0; i < iterations; ++i) {
        const auto found = table.find(key);

        if (found != table.end()) {
            checksum += found->second;
        }

        for (std::uint64_t j = 0; j < extra_work; ++j) {
            checksum = (checksum * 33U) ^ (j + i);
        }

        ++key;
        if (key == table_size) {
            key = 0;
        }
    }

    sink = checksum;

    if (delay_ns > 0) {
        const auto delay_start = std::chrono::steady_clock::now();
        while (static_cast<std::uint64_t>(
                   std::chrono::duration_cast<std::chrono::nanoseconds>(
                       std::chrono::steady_clock::now() - delay_start).count())
               < delay_ns) {
        }
    }

    const auto end = std::chrono::steady_clock::now();

    const auto elapsed_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();

    std::cout
        << "{\"schema_version\":1"
        << ",\"workload\":\"hash_lookup\""
        << ",\"iterations\":" << iterations
        << ",\"extra_work\":" << extra_work
        << ",\"delay_ns\":" << delay_ns
        << ",\"elapsed_ns\":" << elapsed_ns
        << ",\"checksum\":" << static_cast<std::uint64_t>(sink)
        << "}\n";

    return 0;
}
