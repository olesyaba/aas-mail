package ru.olesyaba.aasmail

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LinksTest {
    @Test fun onlyOurServerIsOurs() {
        assertTrue(Links.isOurs("http://127.0.0.1:8780/"))
        assertTrue(Links.isOurs("http://127.0.0.1:8780/index.html#open=main%3Ax"))
        assertFalse(Links.isOurs("http://127.0.0.1:9999/"))          // another app's port
        assertFalse(Links.isOurs("https://127.0.0.1:8780/"))
        assertFalse(Links.isOurs("http://127.0.0.1/"))
        assertFalse(Links.isOurs("http://evil.test/?x=http://127.0.0.1:8780/"))
        assertFalse(Links.isOurs("not a url"))
    }
}
