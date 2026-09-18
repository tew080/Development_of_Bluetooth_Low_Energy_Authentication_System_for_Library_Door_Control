package com.example.application_ble_authentication

//สำหรับอ้างอิงชื่อสิทธิ์ต่างๆที่ประกาศไว้ใน AndroidManifest.xml
import android.Manifest
//สำหรับคำสั่งที่ส่งให้ระบบ Android ทำงาน
import android.content.Intent
//สำหรับใช้ตรวจสอบผลลัพธ์ของสิทธิ์ว่า อนุญาต หรือ ไม่อนุญาต
import android.content.pm.PackageManager
//สำหรับเช็คเวอร์ชัน android api
import android.os.Build

//สำหรับทุกอย่างเกี่ยวกับ Bluetooth พื้นฐาน
import android.bluetooth.*
//สำหรับเครื่องมือสำหรับ BLE โดยเฉพาะ
import android.bluetooth.le.*
//สำหรับตัวห่อหุ้ม UUID
import android.os.ParcelUuid
// นำเข้า Utilities พื้นฐาน
import java.util.*

//สำหรับใช้เช็คสิทธิ์ แบบปลอดภัย ไม่ให้แอปเด้งใน Android รุ่นเก่า
import androidx.core.app.ActivityCompat
//ใช้สั่งขอสิทธิ์ จากผู้ใช้
import androidx.core.content.ContextCompat

import io.flutter.embedding.android.FlutterActivity
//สำหรับกลไกของ Flutter ที่รันอยู่เบื้องหลัง
import io.flutter.embedding.engine.FlutterEngine
//สำหรับ "ท่อสื่อสาร" ที่ใช้ส่งข้อมูลไป-กลับ ระหว่างโค้ด Dart และ Kotlin
import io.flutter.plugin.common.MethodChannel


class MainActivity : FlutterActivity() {
    // ชื่อ Channel ต้องตรงกับฝั่ง Flutter
    private val CHANNEL = "ble_advertiser"

    // รหัสสำหรับขอ Permission
    private val PERMISSION_REQUEST_CODE = 1001

    // รหัสสำหรับขอสิทธิ์แสดง Notification (จำเป็นตั้งแต่ Android 13 / API 33)
    private val NOTIFICATION_PERMISSION_REQUEST_CODE = 1002

    // รหัสสำหรับขอเปิด Bluetooth
    private val ENABLE_BT_REQUEST = 2001

    // ตัวแปรสำหรับจัดการการส่งสัญญาณ BLE
    private var advertiser: BluetoothLeAdvertiser? = null

    // เก็บ Arguments ไว้ชั่วคราว กรณีต้องรอ Permission หรือรอเปิด Bluetooth ก่อน
    private var pendingArgs: Map<String, Any>? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            CHANNEL
        ).setMethodCallHandler { call, result ->
            when (call.method) {

                // กรณีคำสั่งเริ่มส่งสัญญาณ
                "startAdvertising" -> {
                    // แปลง arguments เป็น Map
                    val args = call.arguments as Map<String, Any>
                    // ตรวจสอบ Permission ว่ามีครบหรือไม่
                    if (!hasBlePermission()) {
                        // ถ้าไม่มี ให้เก็บ args ไว้ก่อน แล้วขอ Permission
                        pendingArgs = args

                        requestBlePermission()

                        // สำหรับ android sdk ที่ต่ำกว่า 12(S) อาจต้องอาศัยการเปิด BL
                        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
                            checkBluetooth(args)
                        }
                    } else {
                        //  พร้อมแล้ว → เริ่ม Advertising
                        startAdvertising(args)
                    }
                    // ส่งผลลัพธ์กลับว่ารับเรื่องแล้ว
                    result.success(null)
                }

                // กรณีคำสั่งหยุดส่งสัญญาณ
                "stopAdvertising" -> {
                    stopAdvertising()
                    result.success(null)
                }

                // กรณีคำสั่งอื่นๆ ที่ไม่รู้จัก
                else -> {
                    result.notImplemented()
                }
            }
        }
    }

    // ฟังก์ชันตรวจสอบสิทธิ์
    private fun hasBlePermission(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            return ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.BLUETOOTH_ADVERTISE
            ) == PackageManager.PERMISSION_GRANTED
        } else {
            return ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.BLUETOOTH_ADVERTISE
            ) == PackageManager.PERMISSION_GRANTED &&
                    ContextCompat.checkSelfPermission(
                        this,
                        Manifest.permission.BLUETOOTH
                    ) == PackageManager.PERMISSION_GRANTED &&
                    ContextCompat.checkSelfPermission(
                        this,
                        Manifest.permission.BLUETOOTH_ADMIN
                    ) == PackageManager.PERMISSION_GRANTED &&
                    ContextCompat.checkSelfPermission(
                        this,
                        Manifest.permission.ACCESS_FINE_LOCATION
                    ) == PackageManager.PERMISSION_GRANTED &&
                    ContextCompat.checkSelfPermission(
                        this,
                        Manifest.permission.ACCESS_BACKGROUND_LOCATION
                    ) == PackageManager.PERMISSION_GRANTED
        }
    }

    // ฟังก์ชันขอสิทธิ์
    private fun requestBlePermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(
                    Manifest.permission.BLUETOOTH_ADVERTISE
                ), PERMISSION_REQUEST_CODE
            )
        } else {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(
                    Manifest.permission.BLUETOOTH,
                    Manifest.permission.BLUETOOTH_ADMIN,
                    Manifest.permission.BLUETOOTH_ADVERTISE,
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_BACKGROUND_LOCATION
                ), PERMISSION_REQUEST_CODE
            )
        }
    }

    // ฟังก์ชันตรวจสอบสถานะ Bluetooth และเริ่มทำงาน (สำหรับ android sdk ที่ต่ำกว่า 12(S))
    private fun checkBluetooth(args: Map<String, Any>) {
        val bluetoothManager = getSystemService(BLUETOOTH_SERVICE) as BluetoothManager
        val adapter = bluetoothManager.adapter

        // กรณีเครื่องไม่มี Bluetooth
        if (adapter == null) {
            return
        }

        // เช็คว่า Bluetooth เปิดอยู่หรือไม่
        if (!adapter.isEnabled) {
            // ถ้าปิดอยู่ ให้เก็บ args ไว้ แล้วเด้งขอเปิด Bluetooth
            pendingArgs = args
            val enableBtIntent = Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE)
            startActivityForResult(enableBtIntent, ENABLE_BT_REQUEST)
        }
    }

    // เริ่ม Advertising
    private fun startAdvertising(args: Map<String, Any>) {
        startBackgroundService()
        ensureNotificationPermission()
        val manager = getSystemService(BLUETOOTH_SERVICE) as BluetoothManager
        val adapter = manager.adapter
        advertiser = adapter.bluetoothLeAdvertiser

        val uuid = ParcelUuid.fromString(args["uuid"] as String)
        val companyId = args["companyId"] as Int
        val blekey = args["data"] as String
        val devicename = args["devicename"] as Boolean
        val connectable = args["connectable"] as Boolean
        val txpowerlevel = args["txpowerlevel"] as Boolean

        val settings = AdvertiseSettings.Builder()
            /*  - ADVERTISE_MODE_LOW_POWER
                - ADVERTISE_MODE_BALANCED
                - ADVERTISE_MODE_LOW_LATENCY
            */
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_BALANCED)
            /*  - ADVERTISE_TX_POWER_ULTRA_LOW
                - ADVERTISE_TX_POWER_LOW
                - ADVERTISE_TX_POWER_MEDIUM
                - ADVERTISE_TX_POWER_HIGH
            */
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .setConnectable(connectable)
            .build()

        val data = AdvertiseData.Builder()
            .addServiceUuid(uuid)
            .addManufacturerData(companyId, stringToByteArray(blekey))
            .setIncludeDeviceName(devicename)
            .setIncludeTxPowerLevel(txpowerlevel)
            .build()

        advertiser?.startAdvertising(settings, data, advertiseCallback)
    }

    // ตรวจสอบและขอสิทธิ์แสดง Notification (Android 13 / API 33 ขึ้นไปเท่านั้น)
    // ถ้าไม่ขอ/ผู้ใช้ไม่อนุญาต service ยังทำงานได้ปกติ เพียงแต่ notification จะไม่แสดงบนแถบแจ้งเตือน
    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED

            if (!granted) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    NOTIFICATION_PERMISSION_REQUEST_CODE
                )
            }
        }
    }

    // หยุด Advertising
    private fun stopAdvertising() {
        advertiser?.stopAdvertising(advertiseCallback)
        stopBackgroundService()
    }

    // Callback สำหรับรับผลลัพธ์การส่งสัญญาณ
    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings?) {
            super.onStartSuccess(settingsInEffect)

            // ส่งข้อความกลับไปบอก Flutter ว่าเริ่มทำงานสำเร็จแล้ว
            runOnUiThread {
                flutterEngine?.dartExecutor?.binaryMessenger?.let { messenger ->
                    MethodChannel(messenger, CHANNEL)
                        .invokeMethod("onAdvertisingStarted", null)
                }
            }
        }
    }

    private fun stringToByteArray(keyString: String): ByteArray {
        return keyString.hexToByteArray()
    }

    private fun startBackgroundService() {
        val serviceIntent = Intent(this, BleAdvertisingService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent)
        } else {
            startService(serviceIntent)
        }
    }

    private fun stopBackgroundService() {
        val serviceIntent = Intent(this, BleAdvertisingService::class.java)
        stopService(serviceIntent)
    }
}