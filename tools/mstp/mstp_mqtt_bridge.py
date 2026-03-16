#!/usr/bin/env python3
"""MS/TP to MQTT Bridge

Uses the custom MstpMaster stack to poll MS/TP devices and publish COV changes to MQTT.
"""
import argparse
import json
import logging
import time
import yaml
import sys
import paho.mqtt.client as mqtt

from mstp_master import MstpMaster

logger = logging.getLogger("mstp_bridge")

class MstptoMqttBridge:
    def __init__(self, config_path="config.yaml"):
        # Load config safely
        try:
            with open(config_path) as f:
                self.cfg = yaml.safe_load(f)
        except Exception as e:
            logger.warning("Could not load config.yaml: %s. Using defaults.", e)
            self.cfg = {}

        self.port = self.cfg.get("serial", {}).get("port", "/dev/ttyUSB0")
        self.baud = self.cfg.get("serial", {}).get("baudrate", 38400)
        self.mac = self.cfg.get("serial", {}).get("node_address", 127)

        mqtt_cfg = self.cfg.get("mqtt", {})
        self.broker = mqtt_cfg.get("broker_host", "localhost")
        self.broker_port = mqtt_cfg.get("broker_port", 1883)
        self.topic_prefix = mqtt_cfg.get("topic_prefix", "mstp")

        bridge_cfg = self.cfg.get("bridge", {})
        self.poll_interval = bridge_cfg.get("poll_interval", 30)

        # Mappings: mac -> { 'device_instance': id, ... }
        self.devices = {}
        self.master = MstpMaster(self.port, self.baud, self.mac)
        
        self.mqtt_client = None
        self.cache = {}
        
        self.read_queue = []
        self.queue_index = 0
        self.pending_requests = {} # invoke_id -> (mac, dev_id, obj_type, obj_inst)
        self.next_invoke_id = 1
        
        # Hardcoded quick-scan list if not specified
        self.scan_types = [0, 1, 2, 3, 4, 5] # AI, AO, AV, BI, BO, BV
        self.scan_indices = range(0, 6)

    def connect_mqtt(self):
        self.mqtt_client = mqtt.Client(client_id="mstp_mqtt_bridge")
        if self.cfg.get("mqtt", {}).get("username"):
            self.mqtt_client.username_pw_set(
                self.cfg["mqtt"]["username"],
                self.cfg["mqtt"].get("password", "")
            )
        try:
            self.mqtt_client.connect(self.broker, self.broker_port, 60)
            self.mqtt_client.loop_start()
            logger.info("Connected to MQTT broker %s:%s", self.broker, self.broker_port)
        except Exception as e:
            logger.error("MQTT Connect failed: %s", e)
            self.mqtt_client = None

    def publish_mqtt(self, mac, device_id, obj_type, obj_inst, val):
        if not self.mqtt_client:
            return
        topic = f"{self.topic_prefix}/{device_id}/{obj_type}/{obj_inst}/value"
        payload = json.dumps({
            "mac": mac,
            "device_id": device_id,
            "object_type": obj_type,
            "instance": obj_inst,
            "value": val,
            "timestamp": time.time(),
            "source": "mstp"
        })
        try:
            self.mqtt_client.publish(topic, payload, qos=0, retain=True)
        except Exception as e:
            logger.debug("MQTT publish failed: %s", e)

    def build_poll_queue(self):
        """Build a new queue of reads based on discovered devices."""
        self.read_queue = []
        for mac, info in self.devices.items():
            dev_id = info['device_instance']
            for ot in self.scan_types:
                for oi in self.scan_indices:
                    self.read_queue.append((mac, dev_id, ot, oi, 85)) # 85 = presentValue
        self.queue_index = 0
        self.pending_requests.clear()
        logger.info("Built poll queue with %d reads for %d devices", len(self.read_queue), len(self.devices))

    def on_event(self, event, data):
        if event == 'joined':
            logger.info("Joined token ring! Broadcasting WhoIs...")
            self.master.queue_whois()

        elif event == 'iam':
            mac = data.get('mac')
            dev_id = data.get('device_instance')
            if mac not in self.devices:
                logger.info("Discovered device %s at MAC %d", dev_id, mac)
                self.devices[mac] = data
                self.build_poll_queue()

        elif event == 'token':
            cnt = data['count']
            
            # Periodically rebuild queue and WhoIs (e.g. every 500 tokens ~ 1 minute)
            if cnt > 0 and cnt % 500 == 0:
                self.build_poll_queue()
                self.master.queue_whois()
                logger.info("Periodic refresh: broadcasting WhoIs and rebuilding queue.")
                
            # Pop next read every 2 tokens to avoid suffocating the bus
            if cnt % 2 == 0 and self.read_queue:
                if self.queue_index < len(self.read_queue):
                    mac, dev_id, obj_t, obj_i, pid = self.read_queue[self.queue_index]
                    inv_id = self.next_invoke_id
                    
                    self.pending_requests[inv_id] = (mac, dev_id, obj_t, obj_i)
                    self.master.queue_read_property(mac, dev_id, obj_t, obj_i, pid, invoke_id=inv_id)
                    
                    self.next_invoke_id = (self.next_invoke_id % 255) + 1
                    self.queue_index += 1

        elif event == 'reply':
            val = data.get('value', data.get('value_raw', None))
            inv_id = data.get('invoke_id')
            
            if val is not None and inv_id in self.pending_requests:
                mac, dev_id, req_obj_t, req_obj_i = self.pending_requests.pop(inv_id)
                # The reply might have obj_type string instead of ID, rely on our request params
                # or the parsed reply
                obj_t = data.get('object_type', req_obj_t)
                obj_i = data.get('object_instance', req_obj_i)
                
                cache_key = (dev_id, obj_t, obj_i)
                
                # Check COV (Change of Value)
                if val != self.cache.get(cache_key):
                    self.cache[cache_key] = val
                    logger.info("COV Device %s: %s %s = %s", dev_id, obj_t, obj_i, val)
                    self.publish_mqtt(mac, dev_id, obj_t, obj_i, val)


    def run(self):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        self.connect_mqtt()
        logger.info("Starting MS/TP MQTT Bridge...")
        try:
            # Run indefinitely
            # 86400 * 365 = 1 year
            self.master.run(duration=86400*365, callback=self.on_event)
        except KeyboardInterrupt:
            logger.info("Stopping MS/TP MQTT Bridge...")
        finally:
            if self.mqtt_client:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MS/TP to MQTT Bridge")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()
    
    bridge = MstptoMqttBridge(args.config)
    bridge.run()
