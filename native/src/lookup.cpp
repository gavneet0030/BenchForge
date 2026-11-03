#include <chrono>
#include <charconv>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>

namespace {

constexpr std::uint64_t max_iterations = 100'000'000;
constexpr std::uint64_t max_extra_work = 100'000;
constexpr std::uint64_t max_delay_ns = 5'000'000'000;
constexpr std::uint64_t max_operation_volume = 1'000'000'000;

bool parse_bounded_uint64(
    std::string_view text,
    std::uint64_t maximum,
    std::uint64_t& value
) {
    if (text.empty()) {
        return false;
    }

    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    return result.ec == std::errc{}
        && result.ptr == text.data() + text.size()
        && value <= maximum;
}

}  // namespace

int main(int argc, char* argv[]) {
    std::uint64_t iterations = 100000;
    std::uint64_t extra_work = 0;
    std::uint64_t delay_ns = 0;

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];

        if (arg == "--iterations" && i + 1 < argc) {
            if (!parse_bounded_uint64(argv[++i], max_iterations, iterations)) {
                std::cerr << "iterations must be an integer from 1 to "
                          << max_iterations << '\n';
                return 2;
            }
        } else if (arg == "--extra-work" && i + 1 < argc) {
            if (!parse_bounded_uint64(argv[++i], max_extra_work, extra_work)) {
                std::cerr << "extra-work must be an integer from 0 to "
                          << max_extra_work << '\n';
                return 2;
            }
        } else if (arg == "--delay-ns" && i + 1 < argc) {
            if (!parse_bounded_uint64(argv[++i], max_delay_ns, delay_ns)) {
                std::cerr << "delay-ns must be an integer from 0 to "
                          << max_delay_ns << '\n';
                return 2;
            }
        } else {
            std::cerr << "Unknown or incomplete argument: " << arg << '\n';
            return 2;
        }
    }

    if (iterations == 0) {
        std::cerr << "iterations must be greater than zero\n";
        return 2;
    }

    if (iterations > max_operation_volume / (extra_work + 1)) {
        std::cerr << "iterations times (extra-work + 1) must not exceed "
                  << max_operation_volume << '\n';
        return 2;
    }

    std::unordered_map<std::uint64_t, std::uint64_t> table;
    table.reserve(10000);

    for (std::uint64_t i = 0; i < 10000; ++i) {
        table.emplace(i, i * 17U + 3U);
    }

    std::uint64_t checksum = 0;
    volatile std::uint64_t sink = 0;

    const auto start = std::chrono::steady_clock::now();

    for (std::uint64_t i = 0; i < iterations; ++i) {
        const std::uint64_t key = i % 10000;
        const auto found = table.find(key);

        if (found != table.end()) {
            checksum += found->second;
        }

        for (std::uint64_t j = 0; j < extra_work; ++j) {
            checksum = (checksum * 33U) ^ (j + i);
        }
    }

    // Artificial regression injection, deliberately inside the timed region.
    // This is a controlled perturbation, not a model of every real regression.
    sink = checksum;

    if (delay_ns > 0) {
        const auto delay_start = std::chrono::steady_clock::now();
        while (static_cast<std::uint64_t>(
                   std::chrono::duration_cast<std::chrono::nanoseconds>(
                       std::chrono::steady_clock::now() - delay_start).count())
               < delay_ns) {
            // Busy-wait to avoid coarse sleep scheduling granularity.
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
