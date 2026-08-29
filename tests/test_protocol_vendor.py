"""vendor 体系测试"""

import pytest

from fmo_repeater.protocol.vendor import (
    is_vendor_valid,
    vendor_zone,
)


class TestVendorZone:
    @pytest.mark.parametrize("vendor,zone", [
        (0x0000, "reserved"),
        (0x0001, "reserved"),
        (0x0FFF, "reserved"),
        (0x1000, "experimental"),
        (0x1FFF, "experimental"),
        (0x2000, "software"),
        (0x2FFF, "software"),
        (0x3000, "formal"),
        (0xFFFFFFFF, "formal"),
    ])
    def test_zones(self, vendor, zone):
        assert vendor_zone(vendor) == zone

    def test_out_of_range(self):
        with pytest.raises(ValueError):
            vendor_zone(-1)
        with pytest.raises(ValueError):
            vendor_zone(0x100000000)


class TestIsValid:
    @pytest.mark.parametrize("vendor", [0x0000, 0x0FFF, 0x0ABC])
    def test_reserved_invalid(self, vendor):
        assert is_vendor_valid(vendor) is False

    @pytest.mark.parametrize("vendor", [
        0x1000, 0x1FFF, 0x2000, 0x2FFF, 0x3000, 0xFFFFFFFF,
    ])
    def test_usable(self, vendor):
        assert is_vendor_valid(vendor) is True
