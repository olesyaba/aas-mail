package ru.olesyaba.aasmail

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Account passwords: AES-GCM with a key that never leaves the Android Keystore
 *  (the Mac's Keychain counterpart). Called from Python via Chaquopy. */
object Secrets {
    private const val ALIAS = "aas-secrets"
    private lateinit var prefs: SharedPreferences

    fun init(ctx: Context) {
        prefs = ctx.applicationContext.getSharedPreferences("secrets", Context.MODE_PRIVATE)
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build())
        }.generateKey()
    }

    @JvmStatic fun set(account: String, service: String, password: String) {
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val blob = c.iv + c.doFinal(password.toByteArray())
        prefs.edit().putString("$service/$account", Base64.encodeToString(blob, Base64.NO_WRAP)).apply()
    }

    @JvmStatic fun get(account: String, service: String): String? {
        val blob = Base64.decode(prefs.getString("$service/$account", null) ?: return null, Base64.NO_WRAP)
        return try {
            val c = Cipher.getInstance("AES/GCM/NoPadding")
                .apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, blob, 0, 12)) }
            String(c.doFinal(blob, 12, blob.size - 12))
        } catch (e: Exception) {
            null  // key reset (e.g. restore on a new phone): the user re-enters the password
        }
    }
}
