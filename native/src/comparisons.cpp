#include <algorithm>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <unordered_map>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::uint64_t maximum_items = 10000;

struct Options {
    std::string workload;
    std::uint64_t items = 0;
};

struct Result {
    std::uint64_t checksum = 0;
    std::uint64_t elapsed_ns = 0;
};

std::uint64_t parse_unsigned(std::string_view text, const char* name) {
    std::size_t parsed = 0;
    const auto value = std::stoull(std::string(text), &parsed);
    if (parsed != text.size()) {
        throw std::invalid_argument(std::string(name) + " must be an integer");
    }
    return value;
}

Options parse_options(int argc, char* argv[]) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument(argv[index]);
        if (index + 1 >= argc) {
            throw std::invalid_argument("Missing value for " + std::string(argument));
        }
        const std::string_view value(argv[++index]);
        if (argument == "--workload") {
            options.workload = value;
        } else if (argument == "--items") {
            options.items = parse_unsigned(value, "items");
        } else {
            throw std::invalid_argument("Unknown argument: " + std::string(argument));
        }
    }
    if (options.items == 0 || options.items > maximum_items) {
        throw std::invalid_argument("items must be between 1 and 10000");
    }
    if (options.workload != "sorting" && options.workload != "searching"
        && options.workload != "array_operations" && options.workload != "aggregation"
        && options.workload != "hash_lookup" && options.workload != "file_parsing"
        && options.workload != "numeric") {
        throw std::invalid_argument("Unknown comparison workload");
    }
    return options;
}

std::vector<std::uint64_t> make_values(std::uint64_t items) {
    std::vector<std::uint64_t> values;
    values.reserve(static_cast<std::size_t>(items));
    for (std::uint64_t index = 0; index < items; ++index) {
        values.push_back((index * 1103515245ULL + 12345ULL) ^ (index >> 3));
    }
    return values;
}

std::uint64_t checksum_values(const std::vector<std::uint64_t>& values) {
    std::uint64_t checksum = 0;
    for (const auto value : values) {
        checksum = (checksum * 33U) ^ value;
    }
    return checksum;
}

class TemporaryInputFile {
public:
    TemporaryInputFile() {
        static std::uint64_t sequence = 0;
        const auto root = std::filesystem::temp_directory_path();
        for (int attempt = 0; attempt < 20; ++attempt) {
            const auto stamp = Clock::now().time_since_epoch().count();
            path_ = root / ("benchforge-compare-" + std::to_string(stamp) + "-"
                + std::to_string(sequence++));
            std::error_code error;
            if (!std::filesystem::exists(path_, error) && !error) {
                created_ = true;
                return;
            }
            if (error) {
                throw std::filesystem::filesystem_error(
                    "Could not inspect temporary comparison input", path_, error);
            }
        }
        throw std::runtime_error("Could not allocate a unique comparison input path");
    }

    TemporaryInputFile(const TemporaryInputFile&) = delete;
    TemporaryInputFile& operator=(const TemporaryInputFile&) = delete;

    ~TemporaryInputFile() {
        if (created_) {
            std::error_code error;
            std::filesystem::remove(path_, error);
            if (error) {
                std::cerr << "benchforge_comparisons: temporary input cleanup failed: "
                          << error.message() << '\n';
            }
        }
    }

    const std::filesystem::path& path() const { return path_; }

    void cleanup() {
        std::error_code error;
        std::filesystem::remove(path_, error);
        if (error) {
            throw std::filesystem::filesystem_error(
                "Could not remove temporary comparison input", path_, error);
        }
        created_ = false;
    }

private:
    std::filesystem::path path_;
    bool created_ = false;
};

Result run(const Options& options) {
    auto values = make_values(options.items);
    std::unique_ptr<TemporaryInputFile> input_file;
    if (options.workload == "file_parsing") {
        input_file = std::make_unique<TemporaryInputFile>();
        std::ofstream output(input_file->path(), std::ios::binary | std::ios::trunc);
        if (!output) {
            throw std::runtime_error("Could not create temporary comparison input");
        }
        for (const auto value : values) {
            output << value << '\n';
        }
        output.close();
        if (!output) {
            throw std::runtime_error("Could not write temporary comparison input");
        }
    }

    Result result;
    const auto started = Clock::now();
    if (options.workload == "sorting") {
        std::sort(values.begin(), values.end());
        result.checksum = checksum_values(values);
    } else if (options.workload == "searching") {
        for (std::uint64_t index = 0; index < options.items / 4; ++index) {
            const auto target = values[static_cast<std::size_t>(
                (index * 97 + 11) % options.items)];
            const auto found = std::find(values.begin(), values.end(), target);
            if (found == values.end()) {
                throw std::runtime_error("Search target disappeared");
            }
            result.checksum += static_cast<std::uint64_t>(
                std::distance(values.begin(), found)) + 1;
        }
    } else if (options.workload == "array_operations") {
        for (const auto value : values) {
            result.checksum += value * 3 + 7;
        }
    } else if (options.workload == "aggregation") {
        std::vector<std::uint64_t> totals(32, 0);
        for (std::size_t index = 0; index < values.size(); ++index) {
            totals[index % totals.size()] += values[index];
        }
        result.checksum = checksum_values(totals);
    } else if (options.workload == "hash_lookup") {
        std::unordered_map<std::uint64_t, std::uint64_t> values_by_key;
        values_by_key.reserve(static_cast<std::size_t>(options.items));
        for (std::uint64_t index = 0; index < options.items; ++index) {
            values_by_key.emplace(index, index * 17 + 3);
        }
        for (std::uint64_t index = 0; index < options.items; ++index) {
            result.checksum += values_by_key.at(index);
        }
    } else if (options.workload == "file_parsing") {
        std::ifstream input(input_file->path(), std::ios::binary);
        if (!input) {
            throw std::runtime_error("Could not open temporary comparison input");
        }
        std::uint64_t value = 0;
        while (input >> value) {
            result.checksum += value;
        }
        if (!input.eof()) {
            throw std::runtime_error("Could not parse temporary comparison input");
        }
    } else {
        std::uint64_t state = 0x9e3779b97f4a7c15ULL;
        for (std::uint64_t index = 0; index < options.items; ++index) {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            result.checksum += state ^ index;
        }
    }
    result.elapsed_ns = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            Clock::now() - started).count());
    if (result.elapsed_ns == 0) {
        result.elapsed_ns = 1;
    }
    if (input_file) {
        input_file->cleanup();
    }
    return result;
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        const auto options = parse_options(argc, argv);
        const auto result = run(options);
        std::cout << "{\"schema_version\":1,\"workload\":\""
                  << options.workload << "\",\"items\":" << options.items
                  << ",\"elapsed_ns\":" << result.elapsed_ns
                  << ",\"checksum\":" << result.checksum << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "benchforge_comparisons: " << error.what() << '\n';
        return 2;
    }
}
