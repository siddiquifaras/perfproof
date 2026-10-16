// A fixed amount of json_pointer work: parse, format, compare and look
// up pointers, then unflatten and patch documents. It times nothing
// itself; the caller times the process. The checksum keeps the work
// from being optimized away and is printed so both trees can be
// compared for equal output.
#include <cstdint>
#include <iostream>
#include <string>

#include <nlohmann/json.hpp>

using nlohmann::json;

int main()
{
    json doc;
    for (int f = 0; f < 100; ++f)
    {
        doc["features"][f]["geometry"]["coordinates"] = {f, f + 1};
        doc["features"][f]["properties"]["name~/x"] = std::to_string(f);
    }
    std::uint64_t sum = 0;
    for (int i = 0; i < 200000; ++i)
    {
        const int f = i % 100;
        json::json_pointer ptr(
            "/features/" + std::to_string(f) + "/geometry/coordinates/1");
        sum += ptr.to_string().size();
        sum += doc.at(ptr).get<int>();
        sum += doc.contains(ptr) ? 1 : 0;
        json::json_pointer esc("/features/" + std::to_string(f)
                               + "/properties/name~0~1x");
        sum += esc.to_string().size();
        sum += (ptr / "x" / "y" / 3).to_string().size();
    }
    const json flat = doc.flatten();
    for (int i = 0; i < 200; ++i)
    {
        sum += flat.unflatten().size();
    }
    json ops = json::array();
    for (int i = 0; i < 500; ++i)
    {
        const std::string p = "/features/" + std::to_string(i % 100);
        ops.push_back({{"op", "replace"},
                       {"path", p + "/properties/name~0~1x"},
                       {"value", i}});
        ops.push_back({{"op", "copy"},
                       {"from", p + "/geometry"},
                       {"path", p + "/geometry2"}});
    }
    for (int i = 0; i < 50; ++i)
    {
        sum += doc.patch(ops).size();
    }
    std::cout << sum << "\n";
    return 0;
}
