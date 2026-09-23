/** @brief The RFCHECK diagnostic: noise floor, PA registers, and ten keyed transmits. */

#include "RfCheck.hpp"

#include <algorithm>

#include <Arduino.h>
#include <RadioLib.h>
#include <SPI.h>

#include "BenchState.hpp"

namespace bench {

namespace {

// T-Beam v1.1 SX1278 pinout, mirroring ConnectionLib's LoRaPinConfig defaults. Duplicated rather
// than imported because RFCHECK deliberately owns the radio itself, with no library in the path.
constexpr int kLoRaSck = 5, kLoRaMiso = 19, kLoRaMosi = 27;
constexpr int kLoRaNss = 18, kLoRaReset = 23, kLoRaDio0 = 26;

}   // namespace

void handleRfCheck(bool useRfo) {
    State &s = state();
    // Drives the SX1278 directly rather than reaching inside a Transport: the diagnostic has to
    // work regardless of which stack is loaded, and the radio is a shared resource, so whatever
    // holds it is torn down first.
    s.transport.reset();
    s.configured = false;

    SPIClass spi(HSPI);
    spi.begin(kLoRaSck, kLoRaMiso, kLoRaMosi, kLoRaNss);
    // SX127x::getMod() is protected, and reading the PA registers is the whole point of this
    // diagnostic, so a one-line subclass opens the door rather than patching the library.
    struct ProbeRadio : SX1278 {
        using SX1278::SX1278;
        Module *mod() { return getMod(); }
    };
    ProbeRadio radio(new Module(kLoRaNss, kLoRaDio0, kLoRaReset, RADIOLIB_NC, spi));

    int16_t begun = radio.begin(s.config.frequencyMHz, s.config.bandwidthKHz,
                                 s.config.spreadingFactor, s.config.codingRate,
                                 RADIOLIB_SX127X_SYNC_WORD, s.config.txPowerDbm,
                                 s.config.preambleLength);
    if (begun != RADIOLIB_ERR_NONE) {
        // A chip-not-found here means the radio's rail is down or SPI is miswired - which is a far
        // more useful answer than any RSSI number would have been.
        Serial.printf("ERR,RFCHECK radio.begin failed (%d)\n", static_cast<int>(begun));
        return;
    }
    radio.startReceive();
    Serial.printf("RF,config,%.3f,%.0f,%u,%u,%d\n", static_cast<double>(s.config.frequencyMHz),
                   static_cast<double>(s.config.bandwidthKHz), s.config.spreadingFactor,
                   s.config.codingRate, s.config.txPowerDbm);

    // Read the transmit chain straight out of the chip. Everything upstream reports success - the
    // modem times packets to the microsecond - yet the supply shows no PA draw at all, so the
    // question is what the registers actually contain rather than what the API claims.
    //   PA_CONFIG (0x09): bit 7 = PaSelect (1 = PA_BOOST, 0 = RFO), bits 3:0 = OutputPower
    //   PA_DAC    (0x4D): 0x07 = +20 dBm boost enabled, 0x04 = normal
    //   OCP       (0x0B): bit 5 = OcpOn, bits 4:0 = OcpTrim - an over-current trim set too low
    //                     throttles the PA to almost nothing, which looks exactly like this
    uint8_t paConfig = radio.mod()->SPIreadRegister(RADIOLIB_SX127X_REG_PA_CONFIG);
    uint8_t paDac = radio.mod()->SPIreadRegister(RADIOLIB_SX1278_REG_PA_DAC);
    uint8_t ocp = radio.mod()->SPIreadRegister(RADIOLIB_SX127X_REG_OCP);
    Serial.printf("RF,pa_config,0x%02X,paselect,%s,outputpower,%u\n", paConfig,
                   (paConfig & 0x80) ? "PA_BOOST" : "RFO", static_cast<unsigned>(paConfig & 0x0F));
    Serial.printf("RF,pa_dac,0x%02X\n", paDac);
    Serial.printf("RF,ocp,0x%02X,on,%d,trim_ma,%u\n", ocp, (ocp & 0x20) ? 1 : 0,
                   static_cast<unsigned>((ocp & 0x1F) <= 15 ? 45 + 5 * (ocp & 0x1F)
                                                             : 130 + 10 * ((ocp & 0x1F) - 15)));

    // Noise floor: instantaneous RSSI, no packet. Sampled repeatedly because a single reading
    // catches whatever happened to be on the air in that microsecond.
    float floorMin = 999.0f, floorMax = -999.0f, floorSum = 0.0f;
    const int kSamples = 40;
    for (int i = 0; i < kSamples; i++) {
        float value = radio.getRSSI(false, true);
        floorMin = std::min(floorMin, value);
        floorMax = std::max(floorMax, value);
        floorSum += value;
        delay(5);
    }
    Serial.printf("RF,noisefloor_dbm,%.1f,%.1f,%.1f\n", static_cast<double>(floorMin),
                   static_cast<double>(floorSum / kSamples), static_cast<double>(floorMax));

    // The SX1278 has two transmit pins and only one of them is bonded to the antenna on any given
    // module: PA_BOOST or RFO. RadioLib picks PA_BOOST unless told otherwise, and driving the pin
    // the board did not wire would give exactly what is measured here - correct packet timing,
    // success from transmit(), no supply current, and a signal ~100 dB below expectation from the
    // little that leaks across. So key the PA both ways and let the supply current decide which
    // pin this board actually uses.
    if (useRfo) {
        int16_t state = radio.setOutputPower(s.config.txPowerDbm, true);
        Serial.printf("RF,output_pin,RFO,setOutputPower,%d\n", static_cast<int>(state));
        uint8_t cfg = radio.mod()->SPIreadRegister(RADIOLIB_SX127X_REG_PA_CONFIG);
        Serial.printf("RF,pa_config_after,0x%02X,paselect,%s\n", cfg,
                       (cfg & 0x80) ? "PA_BOOST" : "RFO");
    } else {
        Serial.println("RF,output_pin,PA_BOOST");
    }

    // Transmit half: ten full-size packets, spaced so the PPK2 resolves each one as a step.
    uint8_t buffer[64];
    for (size_t i = 0; i < sizeof(buffer); i++) {
        buffer[i] = static_cast<uint8_t>(i);
    }
    Serial.println("RF,tx_begin,10");
    for (int i = 0; i < 10; i++) {
        uint32_t t0 = micros();
        int16_t txState = radio.transmit(buffer, sizeof(buffer));
        uint32_t elapsed = micros() - t0;
        Serial.printf("RF,tx,%d,%d,%lu\n", i, static_cast<int>(txState),
                       static_cast<unsigned long>(elapsed));
        delay(400);
    }
    radio.startReceive();
    Serial.println("RDY");
}

}   // namespace bench
