package org.example.survcam.activities

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import org.example.survcam.storage.AppPreferences

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val preferences = AppPreferences(this)

        val next = when {
            !preferences.hasSavedServerUrl() -> ServerSetupActivity::class.java
            !preferences.hasValidSession() -> LoginActivity::class.java
            else -> CameraListActivity::class.java
        }

        startActivity(Intent(this, next))
        finish()
    }
}
