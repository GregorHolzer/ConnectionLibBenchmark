#include "raw/RawEspNowTransport.hpp"

#if defined(ESP32)

#include <algorithm>

#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h>

namespace bench {

using connectionlib::Address;
using connectionlib::AddressKind;
using connectionlib::ReceivedMessage;
using connectionlib::SendResult;
using connectionlib::SendStatus;

RawEspNowTransport *RawEspNowTransport::activeInstance_ = nullptr;

RawEspNowTransport::RawEspNowTransport(RawEspNowParams params) : params_(params) {}

RawEspNowTransport::~RawEspNowTransport() {
    if (activeInstance_ == this) {
        if (params_.captureRssi) {
            esp_wifi_set_promiscuous(false);
            esp_wifi_set_promiscuous_rx_cb(nullptr);
        }
        esp_now_unregister_recv_cb();
        esp_now_unregister_send_cb();
        esp_now_deinit();
        activeInstance_ = nullptr;
    }
}

bool RawEspNowTransport::setup() {
    activeInstance_ = this;
    WiFi.mode(WIFI_STA);

    if (params_.wifiChannel != 0) {
        // esp_wifi_set_channel() needs either an associated station or promiscuous mode; the
        // library backend does the same dance for the same reason.
        esp_wifi_set_promiscuous(true);
        esp_wifi_set_channel(params_.wifiChannel, WIFI_SECOND_CHAN_NONE);
        esp_wifi_set_promiscuous(false);
    }

    esp_err_t err = esp_now_init();
    if (err != ESP_OK) {
        lastError_ = err;
        return false;
    }

    esp_now_register_recv_cb(onRecv);
    esp_now_register_send_cb(onSent);

    esp_now_peer_info_t peerInfo{};
    std::copy(params_.peerMac.begin(), params_.peerMac.end(), peerInfo.peer_addr);
    peerInfo.channel = params_.wifiChannel;
    peerInfo.encrypt = params_.encrypt;
    peerInfo.ifidx = WIFI_IF_STA;
    err = esp_now_add_peer(&peerInfo);
    if (err != ESP_OK) {
        lastError_ = err;
        return false;
    }

    if (params_.captureRssi) {
        wifi_promiscuous_filter_t filter{};
        filter.filter_mask = WIFI_PROMIS_FILTER_MASK_DATA;
        esp_wifi_set_promiscuous_filter(&filter);
        esp_wifi_set_promiscuous_rx_cb(onSniff);
        esp_wifi_set_promiscuous(true);
    }
    return true;
}

int RawEspNowTransport::lastErrorCode() const {
    return lastError_;
}

SendResult RawEspNowTransport::send(const Address &destination, const uint8_t *payload, size_t len) {
    // The configured peer MAC is used directly rather than translated out of an Address. That
    // translation is one of the two things this arm exists to leave out.
    (void)destination;

    SendResult result;
    result.payloadBytes = len;
    if (len > kMaxPayload) {
        result.status = SendStatus::PayloadTooLarge;
        return result;
    }

    sendDone_ = false;
    sendStatus_ = ESP_NOW_SEND_FAIL;

    esp_err_t err = esp_now_send(params_.peerMac.data(), payload, len);
    if (err != ESP_OK) {
        lastError_ = err;
        result.status = SendStatus::Failed;
        return result;
    }

    uint32_t waitStart = millis();
    while (!sendDone_ && millis() - waitStart < params_.sendTimeoutMs) {
        delayMicroseconds(100);
    }

    result.timestampMicros = micros();
    result.totalBytes = len;   // ESP-NOW addresses natively, so the library adds nothing here
    if (!sendDone_) {
        result.status = SendStatus::Timeout;
        return result;
    }
    result.acknowledged = (sendStatus_ == ESP_NOW_SEND_SUCCESS);
    result.status = result.acknowledged ? SendStatus::Ok : SendStatus::Failed;
    return result;
}

std::optional<ReceivedMessage> RawEspNowTransport::poll() {
    uint8_t tail = queueTail_.load(std::memory_order_relaxed);
    if (tail == queueHead_.load(std::memory_order_acquire)) {
        return std::nullopt;
    }

    const QueuedMessage &queued = queue_[tail];
    ReceivedMessage msg;
    msg.source = Address::mac(queued.mac);
    msg.payload.assign(queued.data.begin(), queued.data.begin() + queued.len);
    msg.timestampMicros = queued.timestampMicros;
    msg.totalBytes = queued.len;
    if (queued.hasRssi) {
        msg.rssiDbm = static_cast<float>(queued.rssiDbm);
    }

    queueTail_.store(static_cast<uint8_t>((tail + 1) % kQueueCapacity), std::memory_order_release);
    return msg;
}

Address RawEspNowTransport::broadcastAddress() const {
    return Address::broadcast(AddressKind::Mac6);
}

size_t RawEspNowTransport::maxPayloadBytes() const {
    return kMaxPayload;
}

const char *RawEspNowTransport::technologyName() const {
    return "ESP-NOW-raw";
}

float RawEspNowTransport::currentDutyCyclePercent() const {
    // 2.4 GHz under ETSI EN 300 328 carries no duty-cycle limit for adaptive equipment, and
    // 802.11's CSMA/CA qualifies. There is nothing to account for, hence nothing tracked.
    return 0.0f;
}

uint32_t RawEspNowTransport::timeUntilNextAllowedMs(size_t payloadLen) const {
    (void)payloadLen;
    return 0;
}

void RawEspNowTransport::onSniff(void *buf, wifi_promiscuous_pkt_type_t type) {
    if (activeInstance_ == nullptr || buf == nullptr || type != WIFI_PKT_DATA) {
        return;
    }
    auto *packet = static_cast<wifi_promiscuous_pkt_t *>(buf);
    if (packet->rx_ctrl.sig_len < 16) {
        return;
    }
    const uint8_t *source = packet->payload + 10;   // addr2 of the 802.11 MAC header
    std::copy(source, source + 6, activeInstance_->sniffMac_.begin());
    activeInstance_->sniffRssiDbm_ = static_cast<int8_t>(packet->rx_ctrl.rssi);
    activeInstance_->sniffValid_ = true;
}

void RawEspNowTransport::onRecv(const uint8_t *mac, const uint8_t *data, int len) {
    if (activeInstance_ == nullptr || len <= 0) {
        return;
    }
    RawEspNowTransport *self = activeInstance_;
    uint8_t head = self->queueHead_.load(std::memory_order_relaxed);
    uint8_t nextHead = static_cast<uint8_t>((head + 1) % kQueueCapacity);
    if (nextHead == self->queueTail_.load(std::memory_order_acquire)) {
        return;   // full: drop rather than block the WiFi task
    }

    QueuedMessage &slot = self->queue_[head];
    std::copy(mac, mac + 6, slot.mac.begin());
    size_t copyLen = std::min(static_cast<size_t>(len), kMaxPayload);
    std::copy(data, data + copyLen, slot.data.begin());
    slot.len = static_cast<uint8_t>(copyLen);
    slot.timestampMicros = micros();
    slot.hasRssi = false;
    if (self->params_.captureRssi && self->sniffValid_ &&
        std::equal(self->sniffMac_.begin(), self->sniffMac_.end(), mac)) {
        slot.hasRssi = true;
        slot.rssiDbm = self->sniffRssiDbm_;
        self->sniffValid_ = false;
    }
    self->queueHead_.store(nextHead, std::memory_order_release);
}

void RawEspNowTransport::onSent(const uint8_t *mac, esp_now_send_status_t status) {
    (void)mac;
    if (activeInstance_ == nullptr) {
        return;
    }
    activeInstance_->sendStatus_ = status;
    activeInstance_->sendDone_ = true;
}

}   // namespace bench

#endif   // defined(ESP32)
