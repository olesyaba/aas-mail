package ru.olesyaba.aasmail

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SecretsTest {
    @Test fun roundTripAndMissing() {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        Secrets.init(ctx)
        Secrets.set("t", "test-svc", "пароль-1")
        assertEquals("пароль-1", Secrets.get("t", "test-svc"))
        assertNull(Secrets.get("nobody", "test-svc"))
        val raw = ctx.getSharedPreferences("secrets", 0).getString("test-svc/t", "")!!
        assertFalse(raw.contains("пароль"))
    }
}
