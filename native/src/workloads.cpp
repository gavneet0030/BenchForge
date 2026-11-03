#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <ctime>
#include <exception>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <windows.h>
#include <psapi.h>
#include <tlhelp32.h>
#else
#include <arpa/inet.h>
#include <cerrno>
#include <netinet/in.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#endif

namespace {

using Clock = std::chrono::steady_clock;
constexpr std::uint64_t minimum_cpu_utilization_window_ns = 100'000'000;

struct Options {
    std::string workload = "cpu";
    std::uint64_t iterations = 100000;
    std::uint64_t working_set_kib = 1024;
    std::uint64_t file_size_kib = 1024;
};

struct Result {
    std::uint64_t checksum = 0;
    std::uint64_t elapsed_ns = 0;
    std::uint64_t bytes_processed = 0;
    std::uint64_t peak_process_memory_bytes = 0;
    std::uint64_t process_cpu_time_ns = 0;
};

struct PeakMemory {
    std::uint64_t bytes;
    const char* source;
    std::optional<std::uint64_t> current_working_set_bytes;
    std::optional<std::uint64_t> private_process_bytes;
};

std::uint64_t process_cpu_time_ns(std::clock_t started) {
    const auto finished = std::clock();
    if (started == static_cast<std::clock_t>(-1)
        || finished == static_cast<std::clock_t>(-1)
        || finished < started) {
        throw std::runtime_error("Could not measure process CPU time");
    }
    const auto ticks = static_cast<long double>(finished - started);
    const auto nanoseconds = ticks * 1'000'000'000.0L / CLOCKS_PER_SEC;
    if (nanoseconds > static_cast<long double>(
            std::numeric_limits<std::uint64_t>::max())) {
        throw std::runtime_error("Process CPU time measurement overflowed");
    }
    return static_cast<std::uint64_t>(nanoseconds);
}

PeakMemory peak_process_memory() {
#ifdef _WIN32
    PROCESS_MEMORY_COUNTERS_EX counters{};
    counters.cb = sizeof(counters);
    if (!GetProcessMemoryInfo(
            GetCurrentProcess(),
            reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&counters),
            sizeof(counters))) {
        throw std::runtime_error("Could not query process peak working set");
    }
    return {
        static_cast<std::uint64_t>(counters.PeakWorkingSetSize),
        "windows_peak_working_set",
        static_cast<std::uint64_t>(counters.WorkingSetSize),
        static_cast<std::uint64_t>(counters.PrivateUsage),
    };
#else
    rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage) != 0 || usage.ru_maxrss < 0) {
        throw std::runtime_error("Could not query process peak resident memory");
    }
#ifdef __APPLE__
    constexpr std::uint64_t bytes_per_unit = 1;
    constexpr const char* source = "macos_peak_rss";
#else
    constexpr std::uint64_t bytes_per_unit = 1024;
    constexpr const char* source = "posix_peak_rss";
#endif
    return {
        static_cast<std::uint64_t>(usage.ru_maxrss) * bytes_per_unit,
        source,
        std::nullopt,
        std::nullopt,
    };
#endif
}

std::optional<std::uint64_t> process_thread_count() {
#ifdef _WIN32
    const HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        throw std::runtime_error("Could not query process thread count");
    }
    THREADENTRY32 entry{};
    entry.dwSize = sizeof(entry);
    std::uint64_t count = 0;
    if (Thread32First(snapshot, &entry)) {
        do {
            if (entry.th32OwnerProcessID == GetCurrentProcessId()) {
                ++count;
            }
            entry.dwSize = sizeof(entry);
        } while (Thread32Next(snapshot, &entry));
        if (GetLastError() != ERROR_NO_MORE_FILES) {
            CloseHandle(snapshot);
            throw std::runtime_error("Could not finish querying process thread count");
        }
    } else if (GetLastError() != ERROR_NO_MORE_FILES) {
        CloseHandle(snapshot);
        throw std::runtime_error("Could not read process thread count");
    }
    CloseHandle(snapshot);
    return count;
#elif defined(__linux__)
    std::error_code error;
    const std::filesystem::directory_iterator end;
    std::filesystem::directory_iterator current("/proc/self/task", error);
    if (error) {
        throw std::filesystem::filesystem_error(
            "Could not query process thread count", "/proc/self/task", error);
    }
    std::uint64_t count = 0;
    while (current != end) {
        ++count;
        current.increment(error);
        if (error) {
            throw std::filesystem::filesystem_error(
                "Could not read process thread count", "/proc/self/task", error);
        }
    }
    return count;
#else
    return std::nullopt;
#endif
}

void write_optional_json_number(
    std::ostream& output,
    const std::optional<std::uint64_t>& value
) {
    if (value) {
        output << *value;
    } else {
        output << "null";
    }
}

#ifdef _WIN32
using Socket = SOCKET;
constexpr Socket invalid_socket = INVALID_SOCKET;
void close_socket(Socket socket) { closesocket(socket); }
int socket_error() { return WSAGetLastError(); }
class SocketRuntime {
public:
    SocketRuntime() {
        WSADATA data{};
        if (WSAStartup(MAKEWORD(2, 2), &data) != 0) {
            throw std::runtime_error("Winsock startup failed");
        }
    }
    ~SocketRuntime() { WSACleanup(); }
};
#else
using Socket = int;
constexpr Socket invalid_socket = -1;
void close_socket(Socket socket) { ::close(socket); }
int socket_error() { return errno; }
class SocketRuntime {};
#endif

void set_socket_timeout(Socket socket) {
#ifdef _WIN32
    const DWORD timeout_ms = 5000;
    if (setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO,
            reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms)) == SOCKET_ERROR
        || setsockopt(socket, SOL_SOCKET, SO_SNDTIMEO,
            reinterpret_cast<const char*>(&timeout_ms), sizeof(timeout_ms)) == SOCKET_ERROR) {
#else
    const timeval timeout{5, 0};
    if (setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0
        || setsockopt(socket, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) < 0) {
#endif
        throw std::runtime_error("Could not configure loopback socket timeout");
    }
}

void send_all(Socket socket, const char* data, std::size_t length) {
    std::size_t sent = 0;
    while (sent < length) {
#ifdef _WIN32
        const int count = send(socket, data + sent, static_cast<int>(length - sent), 0);
        if (count == SOCKET_ERROR || count == 0) {
#else
        const auto count = send(socket, data + sent, length - sent, 0);
        if (count < 0 || count == 0) {
#endif
            throw std::runtime_error(
                "Loopback socket send failed: " + std::to_string(socket_error()));
        }
        sent += static_cast<std::size_t>(count);
    }
}

void receive_all(Socket socket, char* data, std::size_t length) {
    std::size_t received = 0;
    while (received < length) {
#ifdef _WIN32
        const int count = recv(
            socket, data + received, static_cast<int>(length - received), 0);
        if (count == SOCKET_ERROR || count == 0) {
#else
        const auto count = recv(socket, data + received, length - received, 0);
        if (count < 0 || count == 0) {
#endif
            throw std::runtime_error(
                "Loopback socket receive failed: " + std::to_string(socket_error()));
        }
        received += static_cast<std::size_t>(count);
    }
}

std::uint64_t parse_unsigned(std::string_view text, const char* name) {
    std::uint64_t value = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), value);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
        throw std::invalid_argument(std::string(name) + " must be a non-negative integer");
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
        } else if (argument == "--iterations") {
            options.iterations = parse_unsigned(value, "iterations");
        } else if (argument == "--working-set-kib") {
            options.working_set_kib = parse_unsigned(value, "working-set-kib");
        } else if (argument == "--file-size-kib") {
            options.file_size_kib = parse_unsigned(value, "file-size-kib");
        } else {
            throw std::invalid_argument("Unknown argument: " + std::string(argument));
        }
    }
    if (options.iterations == 0) {
        throw std::invalid_argument("iterations must be greater than zero");
    }
    if (options.workload == "cpu" && options.iterations > 100000000) {
        throw std::invalid_argument("CPU iterations must not exceed 100000000");
    }
    if (options.workload == "tcp_loopback" && options.iterations > 100000) {
        throw std::invalid_argument("tcp_loopback iterations must not exceed 100000");
    }
    if (options.workload == "memory") {
        if (options.iterations > 1000) {
            throw std::invalid_argument("memory iterations must not exceed 1000");
        }
        if (options.working_set_kib == 0 || options.working_set_kib > 65536) {
            throw std::invalid_argument("working-set-kib must be between 1 and 65536");
        }
        if (options.working_set_kib * options.iterations > 262144) {
            throw std::invalid_argument(
                "memory working-set-kib times iterations must not exceed 262144");
        }
    }
    if (options.workload == "file_io") {
        if (options.iterations > 16) {
            throw std::invalid_argument("file_io iterations must not exceed 16");
        }
        if (options.file_size_kib == 0 || options.file_size_kib > 16384) {
            throw std::invalid_argument("file-size-kib must be between 1 and 16384");
        }
        if (options.file_size_kib * options.iterations > 131072) {
            throw std::invalid_argument(
                "file-size-kib times iterations must not exceed 131072");
        }
    }
    if (options.workload != "cpu" && options.workload != "memory"
        && options.workload != "file_io" && options.workload != "tcp_loopback") {
        throw std::invalid_argument(
            "workload must be cpu, memory, file_io, or tcp_loopback");
    }
    return options;
}

Result run_cpu(std::uint64_t iterations) {
    std::uint64_t state = 0x9e3779b97f4a7c15ULL;
    std::uint64_t checksum = 0;
    const auto start = Clock::now();
    const auto cpu_start = std::clock();
    for (std::uint64_t index = 0; index < iterations; ++index) {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        checksum += state ^ index;
    }
    const auto elapsed = Clock::now() - start;
    return {
        checksum,
        static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed).count()),
        0,
        0,
        process_cpu_time_ns(cpu_start),
    };
}

Result run_memory(std::uint64_t iterations, std::uint64_t working_set_kib) {
    constexpr std::uint64_t kib = 1024;
    const auto word_count = working_set_kib * kib / sizeof(std::uint64_t);
    std::vector<std::uint64_t> values(static_cast<std::size_t>(word_count));
    for (std::uint64_t index = 0; index < word_count; ++index) {
        values[static_cast<std::size_t>(index)] = index + 1;
    }

    std::uint64_t checksum = 0;
    const auto start = Clock::now();
    const auto cpu_start = std::clock();
    for (std::uint64_t pass = 0; pass < iterations; ++pass) {
        for (auto& value : values) {
            value += pass + 1;
            checksum ^= value;
        }
    }
    const auto elapsed = Clock::now() - start;
    const auto bytes = word_count * sizeof(std::uint64_t) * iterations * 2;
    return {
        checksum,
        static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed).count()),
        bytes,
        0,
        process_cpu_time_ns(cpu_start),
    };
}

class TemporaryWorkspace {
public:
    TemporaryWorkspace() {
        static std::atomic<std::uint64_t> sequence{0};
        const auto root = std::filesystem::temp_directory_path();
        for (int attempt = 0; attempt < 20; ++attempt) {
            const auto stamp = Clock::now().time_since_epoch().count();
            path_ = root / ("benchforge-" + std::to_string(stamp) + "-"
                + std::to_string(sequence.fetch_add(1)));
            std::error_code error;
            if (std::filesystem::create_directory(path_, error)) {
                created_ = true;
                return;
            }
            if (error) {
                throw std::filesystem::filesystem_error(
                    "Could not create a private temporary directory", path_, error);
            }
        }
        throw std::runtime_error("Could not allocate a unique temporary directory");
    }

    TemporaryWorkspace(const TemporaryWorkspace&) = delete;
    TemporaryWorkspace& operator=(const TemporaryWorkspace&) = delete;

    ~TemporaryWorkspace() {
        if (created_) {
            std::error_code ignored;
            std::filesystem::remove_all(path_, ignored);
            if (ignored) {
                std::cerr << "benchforge_workloads: temporary workspace cleanup failed: "
                          << ignored.message() << '\n';
            }
        }
    }

    const std::filesystem::path& path() const { return path_; }

    void cleanup() {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
        if (error) {
            throw std::filesystem::filesystem_error(
                "Could not remove the temporary benchmark directory", path_, error);
        }
        created_ = false;
    }

private:
    std::filesystem::path path_;
    bool created_ = false;
};

Result run_file_io(std::uint64_t iterations, std::uint64_t file_size_kib) {
    constexpr std::size_t block_size = 64 * 1024;
    const auto file_size = static_cast<std::size_t>(file_size_kib * 1024);
    std::vector<char> block(block_size);
    for (std::size_t index = 0; index < block.size(); ++index) {
        block[index] = static_cast<char>((index * 31 + 7) & 0xff);
    }

    TemporaryWorkspace workspace;
    const auto file = workspace.path() / "io.bin";
    std::uint64_t checksum = 0;
    const auto start = Clock::now();
    const auto cpu_start = std::clock();
    for (std::uint64_t pass = 0; pass < iterations; ++pass) {
        {
            std::ofstream output(file, std::ios::binary | std::ios::trunc);
            if (!output) {
                throw std::runtime_error("Could not open temporary file for writing");
            }
            std::size_t remaining = file_size;
            while (remaining > 0) {
                const auto count = std::min(remaining, block.size());
                output.write(block.data(), static_cast<std::streamsize>(count));
                if (!output) {
                    throw std::runtime_error("Temporary file write failed");
                }
                remaining -= count;
            }
            output.flush();
            if (!output) {
                throw std::runtime_error("Temporary file flush failed");
            }
        }
        {
            std::ifstream input(file, std::ios::binary);
            if (!input) {
                throw std::runtime_error("Could not open temporary file for reading");
            }
            std::size_t remaining = file_size;
            while (remaining > 0) {
                const auto count = std::min(remaining, block.size());
                input.read(block.data(), static_cast<std::streamsize>(count));
                if (input.gcount() != static_cast<std::streamsize>(count)) {
                    throw std::runtime_error("Temporary file read was incomplete");
                }
                for (std::size_t index = 0; index < count; ++index) {
                    checksum = (checksum * 33U) ^ static_cast<unsigned char>(block[index]);
                }
                remaining -= count;
            }
        }
    }
    const auto elapsed = Clock::now() - start;
    const auto cpu_time_ns = process_cpu_time_ns(cpu_start);
    workspace.cleanup();
    const auto bytes = static_cast<std::uint64_t>(file_size) * iterations * 2;
    return {
        checksum,
        static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed).count()),
        bytes,
        0,
        cpu_time_ns,
    };
}

Result run_tcp_loopback(std::uint64_t iterations) {
    SocketRuntime runtime;
    const Socket listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener == invalid_socket) {
        throw std::runtime_error(
            "Could not create loopback listener: " + std::to_string(socket_error()));
    }
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = 0;
    if (bind(listener, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0
        || listen(listener, 1) != 0) {
        const auto error = socket_error();
        close_socket(listener);
        throw std::runtime_error(
            "Could not bind loopback listener: " + std::to_string(error));
    }
#ifdef _WIN32
    int address_length = sizeof(address);
#else
    socklen_t address_length = sizeof(address);
#endif
    if (getsockname(listener, reinterpret_cast<sockaddr*>(&address), &address_length) != 0) {
        const auto error = socket_error();
        close_socket(listener);
        throw std::runtime_error(
            "Could not inspect loopback listener: " + std::to_string(error));
    }

    const Socket client = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (client == invalid_socket) {
        close_socket(listener);
        throw std::runtime_error(
            "Could not create loopback client: " + std::to_string(socket_error()));
    }
    try {
        set_socket_timeout(client);
        if (connect(client, reinterpret_cast<const sockaddr*>(&address),
                static_cast<int>(sizeof(address))) != 0) {
            throw std::runtime_error(
                "Could not connect to loopback listener: " + std::to_string(socket_error()));
        }
    } catch (...) {
        close_socket(client);
        close_socket(listener);
        throw;
    }

    std::promise<void> accepted_promise;
    auto accepted = accepted_promise.get_future();
    std::exception_ptr server_error;
    std::thread server([&] {
        Socket peer = invalid_socket;
        try {
            peer = accept(listener, nullptr, nullptr);
            if (peer == invalid_socket) {
                throw std::runtime_error(
                    "Loopback accept failed: " + std::to_string(socket_error()));
            }
            set_socket_timeout(peer);
            accepted_promise.set_value();
            std::array<char, 64> request{};
            for (std::uint64_t index = 0; index < iterations; ++index) {
                receive_all(peer, request.data(), request.size());
                send_all(peer, request.data(), request.size());
            }
        } catch (...) {
            server_error = std::current_exception();
            try {
                accepted_promise.set_exception(server_error);
            } catch (const std::future_error&) {
            }
        }
        if (peer != invalid_socket) {
            close_socket(peer);
        }
    });

    Result result;
    try {
        if (accepted.wait_for(std::chrono::seconds(6)) != std::future_status::ready) {
            throw std::runtime_error("Loopback server did not accept the connection");
        }
        accepted.get();
        std::array<char, 64> request{};
        std::array<char, 64> response{};
        for (std::size_t index = 0; index < request.size(); ++index) {
            request[index] = static_cast<char>((index * 17 + 3) & 0xff);
        }
        const auto start = Clock::now();
        const auto cpu_start = std::clock();
        for (std::uint64_t index = 0; index < iterations; ++index) {
            send_all(client, request.data(), request.size());
            receive_all(client, response.data(), response.size());
            if (response != request) {
                throw std::runtime_error("Loopback echo checksum validation failed");
            }
            for (const auto byte : response) {
                result.checksum = (result.checksum * 33U)
                    ^ static_cast<unsigned char>(byte);
            }
        }
        const auto elapsed = Clock::now() - start;
        result.elapsed_ns = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed).count());
        result.process_cpu_time_ns = process_cpu_time_ns(cpu_start);
        result.bytes_processed = iterations * request.size() * 2;
    } catch (...) {
#ifdef _WIN32
        shutdown(client, SD_BOTH);
#else
        shutdown(client, SHUT_RDWR);
#endif
        close_socket(client);
        server.join();
        close_socket(listener);
        throw;
    }
#ifdef _WIN32
    shutdown(client, SD_BOTH);
#else
    shutdown(client, SHUT_RDWR);
#endif
    close_socket(client);
    server.join();
    close_socket(listener);
    if (server_error) {
        std::rethrow_exception(server_error);
    }
    return result;
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        const auto options = parse_options(argc, argv);
        Result result;
        if (options.workload == "cpu") {
            result = run_cpu(options.iterations);
        } else if (options.workload == "memory") {
            result = run_memory(options.iterations, options.working_set_kib);
        } else if (options.workload == "tcp_loopback") {
            result = run_tcp_loopback(options.iterations);
        } else {
            result = run_file_io(options.iterations, options.file_size_kib);
        }
        if (result.elapsed_ns == 0) {
            result.elapsed_ns = 1;
        }
        const auto peak_memory = peak_process_memory();
        const auto thread_count = process_thread_count();
        result.peak_process_memory_bytes = peak_memory.bytes;
        const auto cpu_measurements_available =
            result.elapsed_ns >= minimum_cpu_utilization_window_ns;
        const auto cpu_utilization_percent = cpu_measurements_available
            ? 100.0 * static_cast<double>(result.process_cpu_time_ns)
                / static_cast<double>(result.elapsed_ns)
            : 0.0;
        std::cout << "{\"schema_version\":1,\"workload\":\"" << options.workload
                  << "\",\"iterations\":" << options.iterations
                  << ",\"elapsed_ns\":" << result.elapsed_ns
                  << ",\"checksum\":" << result.checksum
                  << ",\"bytes_processed\":" << result.bytes_processed
                  << ",\"peak_process_memory_bytes\":"
                  << result.peak_process_memory_bytes
                  << ",\"peak_process_memory_source\":\""
                  << peak_memory.source << "\""
                  << ",\"current_working_set_bytes\":";
        write_optional_json_number(std::cout, peak_memory.current_working_set_bytes);
        std::cout << ",\"private_process_bytes\":";
        write_optional_json_number(std::cout, peak_memory.private_process_bytes);
        std::cout << ",\"thread_count\":";
        write_optional_json_number(std::cout, thread_count);
        std::cout
                  << ",\"process_cpu_time_ns\":";
        if (cpu_measurements_available) {
            std::cout << result.process_cpu_time_ns;
        } else {
            std::cout << "null";
        }
        std::cout
                  << ",\"cpu_utilization_percent\":";
        if (cpu_measurements_available) {
            std::cout << cpu_utilization_percent;
        } else {
            std::cout << "null";
        }
        std::cout
                  << ",\"working_set_kib\":" << options.working_set_kib
                  << ",\"file_size_kib\":" << options.file_size_kib << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "benchforge_workloads: " << error.what() << '\n';
        return 2;
    }
}
