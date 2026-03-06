/**
 * MqttConfig.js — MQTT broker configuration and connection test.
 */

const MqttConfig = (() => {

    async function load() {
        try {
            const cfg = await App.api('/api/mqtt/config');

            document.getElementById('mqtt-host').value = cfg.broker_host || 'localhost';
            document.getElementById('mqtt-port').value = cfg.broker_port || 1883;
            document.getElementById('mqtt-username').value = cfg.username || '';
            document.getElementById('mqtt-password').value = cfg.password || '';
            document.getElementById('mqtt-client-id').value = cfg.client_id || 'bacnet_mqtt_gateway';
            document.getElementById('mqtt-topic-prefix').value = cfg.topic_prefix || 'bacnet';
            document.getElementById('mqtt-qos').value = String(cfg.qos ?? 1);
            document.getElementById('mqtt-tls').checked = !!cfg.use_tls;
            document.getElementById('mqtt-retain').checked = !!cfg.retain;

            updateStatusDisplay();
        } catch (e) {
            console.error('MQTT config load error:', e);
        }
    }

    async function save(event) {
        event.preventDefault();

        const payload = {
            broker_host: document.getElementById('mqtt-host').value,
            broker_port: parseInt(document.getElementById('mqtt-port').value),
            username: document.getElementById('mqtt-username').value,
            password: document.getElementById('mqtt-password').value,
            client_id: document.getElementById('mqtt-client-id').value,
            topic_prefix: document.getElementById('mqtt-topic-prefix').value,
            qos: parseInt(document.getElementById('mqtt-qos').value),
            use_tls: document.getElementById('mqtt-tls').checked,
            retain: document.getElementById('mqtt-retain').checked,
        };

        try {
            await App.api('/api/mqtt/config', 'PUT', payload);
            App.toast('MQTT config saved & reconnecting…', 'success');
            setTimeout(updateStatusDisplay, 2000);
        } catch (e) {
            App.toast('Save failed: ' + e.message, 'error');
        }
    }

    async function test() {
        const payload = {
            broker_host: document.getElementById('mqtt-host').value,
            broker_port: parseInt(document.getElementById('mqtt-port').value),
            username: document.getElementById('mqtt-username').value,
            password: document.getElementById('mqtt-password').value,
            use_tls: document.getElementById('mqtt-tls').checked,
        };

        const icon = document.getElementById('mqtt-status-icon');
        const text = document.getElementById('mqtt-status-text');
        const detail = document.getElementById('mqtt-status-detail');

        icon.textContent = '⏳';
        text.textContent = 'Testing…';
        detail.textContent = 'Connecting to broker…';
        detail.className = 'badge badge-warning';

        try {
            const result = await App.api('/api/mqtt/test', 'POST', payload);

            if (result.success) {
                icon.textContent = '✅';
                text.textContent = 'Connection Successful';
                detail.textContent = result.message;
                detail.className = 'badge badge-success';
            } else {
                icon.textContent = '❌';
                text.textContent = 'Connection Failed';
                detail.textContent = result.message;
                detail.className = 'badge badge-error';
            }
        } catch (e) {
            icon.textContent = '❌';
            text.textContent = 'Test Failed';
            detail.textContent = e.message;
            detail.className = 'badge badge-error';
        }
    }

    async function updateStatusDisplay() {
        try {
            const status = await App.api('/api/status');
            const icon = document.getElementById('mqtt-status-icon');
            const text = document.getElementById('mqtt-status-text');
            const detail = document.getElementById('mqtt-status-detail');

            if (status.mqtt_connected) {
                icon.textContent = '✅';
                text.textContent = 'Connected';
                detail.textContent = 'Broker connection active';
                detail.className = 'badge badge-success';
            } else {
                icon.textContent = '📡';
                text.textContent = 'Disconnected';
                detail.textContent = 'Not connected to broker';
                detail.className = 'badge badge-warning';
            }
        } catch (e) {
            // ignore
        }
    }

    return { load, save, test };
})();
