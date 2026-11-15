package com.christ.netgrab

import android.Manifest
import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.view.View
import android.webkit.MimeTypeMap
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException

class MainActivity : AppCompatActivity() {

    private lateinit var urlInput: EditText
    private lateinit var pasteBtn: Button
    private lateinit var qualitySpinner: Spinner
    private lateinit var downloadBtn: Button
    private lateinit var updateBtn: Button
    private lateinit var progressBar: ProgressBar
    private lateinit var statusText: TextView

    private var engineReady = false
    private var busy = false
    private var cancelled = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        urlInput = findViewById(R.id.urlInput)
        pasteBtn = findViewById(R.id.pasteBtn)
        qualitySpinner = findViewById(R.id.qualitySpinner)
        downloadBtn = findViewById(R.id.downloadBtn)
        updateBtn = findViewById(R.id.updateBtn)
        progressBar = findViewById(R.id.progressBar)
        statusText = findViewById(R.id.statusText)

        pasteBtn.setOnClickListener { pasteFromClipboard() }
        downloadBtn.setOnClickListener { if (busy) cancelDownload() else startDownload() }
        updateBtn.setOnClickListener { updateEngine(manual = true) }

        handleShare(intent)
        initEngine()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    /** Lien reçu via "Partager" depuis une autre appli. */
    private fun handleShare(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND) {
            val shared = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
            extractUrl(shared)?.let { urlInput.setText(it) }
        }
    }

    private fun extractUrl(text: String): String? =
        URL_REGEX.find(text)?.value

    private fun pasteFromClipboard() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = clipboard.primaryClip
        if (clip != null && clip.itemCount > 0) {
            val text = clip.getItemAt(0).coerceToText(this).toString()
            urlInput.setText(extractUrl(text) ?: text)
        }
    }

    private fun initEngine() {
        statusText.setText(R.string.st_preparing)
        lifecycleScope.launch {
            val error = withContext(Dispatchers.IO) {
                try {
                    YoutubeDL.getInstance().init(applicationContext)
                    FFmpeg.getInstance().init(applicationContext)
                    null
                } catch (e: Exception) {
                    e.message ?: e.toString()
                }
            }
            if (error != null) {
                statusText.text = getString(R.string.st_engine_error, error)
                return@launch
            }
            engineReady = true
            statusText.setText(R.string.st_ready)

            // Les réseaux changent souvent : on met yt-dlp à jour une fois par jour.
            val prefs = getPreferences(Context.MODE_PRIVATE)
            val last = prefs.getLong(PREF_LAST_UPDATE, 0L)
            if (System.currentTimeMillis() - last > 24L * 60 * 60 * 1000) {
                updateEngine(manual = false)
            }
        }
    }

    private fun updateEngine(manual: Boolean) {
        if (!engineReady || busy) return
        updateBtn.isEnabled = false
        statusText.setText(R.string.st_updating)
        lifecycleScope.launch {
            val error = withContext(Dispatchers.IO) {
                try {
                    YoutubeDL.getInstance()
                        .updateYoutubeDL(applicationContext, YoutubeDL.UpdateChannel.STABLE)
                    null
                } catch (e: Exception) {
                    e.message ?: e.toString()
                }
            }
            updateBtn.isEnabled = true
            if (busy) return@launch
            if (error == null) {
                getPreferences(Context.MODE_PRIVATE).edit()
                    .putLong(PREF_LAST_UPDATE, System.currentTimeMillis()).apply()
                statusText.setText(if (manual) R.string.st_updated else R.string.st_ready)
            } else {
                statusText.text =
                    if (manual) getString(R.string.st_update_failed, error)
                    else getString(R.string.st_ready)
            }
        }
    }

    private fun startDownload() {
        val url = extractUrl(urlInput.text.toString())
        if (url == null) {
            statusText.setText(R.string.st_no_url)
            return
        }
        if (!engineReady) {
            statusText.setText(R.string.st_not_ready)
            return
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), 1
            )
            statusText.setText(R.string.st_permission)
            return
        }

        val quality = qualitySpinner.selectedItemPosition
        cancelled = false
        setBusy(true)
        statusText.setText(R.string.st_starting)

        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    Result.success(download(url, quality))
                } catch (e: Exception) {
                    Result.failure(e)
                }
            }
            setBusy(false)
            when {
                cancelled -> statusText.setText(R.string.st_cancelled)
                result.isSuccess ->
                    statusText.text = getString(
                        R.string.st_done, result.getOrNull().orEmpty().joinToString("\n")
                    )
                else -> {
                    val e = result.exceptionOrNull()
                    val msg = (e?.message ?: e.toString()).trim().takeLast(600)
                    statusText.text = getString(R.string.st_failed, msg)
                }
            }
        }
    }

    private fun cancelDownload() {
        cancelled = true
        try {
            YoutubeDL.getInstance().destroyProcessById(PROCESS_ID)
        } catch (_: Exception) {
        }
    }

    /** Exécuté hors du thread principal. Retourne les emplacements des fichiers enregistrés. */
    private fun download(url: String, quality: Int): List<String> {
        val tmp = File(getExternalFilesDir(null) ?: filesDir, "tmp")
        tmp.deleteRecursively()
        tmp.mkdirs()

        val audioOnly = quality == 4
        val request = YoutubeDLRequest(url)
        request.addOption("--no-playlist")
        request.addOption("--no-mtime")
        request.addOption("-o", tmp.absolutePath + "/%(title).80s [%(id)s].%(ext)s")

        if (audioOnly) {
            request.addOption("-f", "ba/b")
            request.addOption("-x")
            request.addOption("--audio-format", "mp3")
            request.addOption("--audio-quality", "0")
        } else {
            // La résolution passe en premier ; à résolution égale on préfère
            // H.264 + AAC pour que la vidéo se lise partout (galerie, WhatsApp…).
            val sort = when (quality) {
                1 -> "res:1080,vcodec:h264,acodec:m4a"
                2 -> "res:720,vcodec:h264,acodec:m4a"
                3 -> "res:480,vcodec:h264,acodec:m4a"
                else -> "res,fps,vcodec:h264,acodec:m4a"
            }
            request.addOption("-f", "bv*+ba/b")
            request.addOption("-S", sort)
            request.addOption("--merge-output-format", "mp4")
        }

        YoutubeDL.getInstance().execute(request, PROCESS_ID) { progress, etaInSeconds, _ ->
            runOnUiThread { showProgress(progress, etaInSeconds) }
        }

        val files = tmp.listFiles()?.filter { it.isFile && !it.name.endsWith(".part") }.orEmpty()
        if (files.isEmpty()) throw IOException("Aucun fichier produit.")
        val saved = files.map { saveToGallery(it, audioOnly) }
        tmp.deleteRecursively()
        return saved
    }

    private fun showProgress(progress: Float, etaInSeconds: Long) {
        if (!busy) return
        if (progress <= 0f) return
        val p = progress.toInt().coerceIn(0, 100)
        progressBar.isIndeterminate = false
        progressBar.progress = p
        if (p >= 100) {
            statusText.setText(R.string.st_finishing)
        } else {
            val eta = if (etaInSeconds > 0) {
                "%d:%02d".format(etaInSeconds / 60, etaInSeconds % 60)
            } else "…"
            statusText.text = getString(R.string.st_progress, p, eta)
        }
    }

    /** Copie le fichier dans Films/NetGrab ou Musique/NetGrab pour qu'il apparaisse dans la galerie. */
    private fun saveToGallery(file: File, audio: Boolean): String {
        val ext = file.extension.lowercase()
        val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext)
            ?: if (audio) "audio/mpeg" else "video/mp4"
        val baseDir = when {
            mime.startsWith("audio/") -> Environment.DIRECTORY_MUSIC
            mime.startsWith("video/") -> Environment.DIRECTORY_MOVIES
            else -> Environment.DIRECTORY_DOWNLOADS
        }
        val relative = "$baseDir/$FOLDER"

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val volume = MediaStore.VOLUME_EXTERNAL_PRIMARY
            val collection: Uri = when (baseDir) {
                Environment.DIRECTORY_MUSIC -> MediaStore.Audio.Media.getContentUri(volume)
                Environment.DIRECTORY_MOVIES -> MediaStore.Video.Media.getContentUri(volume)
                else -> MediaStore.Downloads.getContentUri(volume)
            }
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, file.name)
                put(MediaStore.MediaColumns.MIME_TYPE, mime)
                put(MediaStore.MediaColumns.RELATIVE_PATH, relative)
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val uri = contentResolver.insert(collection, values)
                ?: throw IOException("Impossible de créer le fichier dans $relative")
            val out = contentResolver.openOutputStream(uri)
                ?: throw IOException("Impossible d'écrire dans $relative")
            out.use { o -> file.inputStream().use { it.copyTo(o) } }
            values.clear()
            values.put(MediaStore.MediaColumns.IS_PENDING, 0)
            contentResolver.update(uri, values, null, null)
        } else {
            @Suppress("DEPRECATION")
            val dir = File(Environment.getExternalStoragePublicDirectory(baseDir), FOLDER)
            dir.mkdirs()
            val target = File(dir, file.name)
            file.copyTo(target, overwrite = true)
            MediaScannerConnection.scanFile(this, arrayOf(target.absolutePath), arrayOf(mime), null)
        }
        file.delete()
        return "$relative/${file.name}"
    }

    private fun setBusy(value: Boolean) {
        busy = value
        downloadBtn.setText(if (value) R.string.cancel else R.string.download)
        pasteBtn.isEnabled = !value
        updateBtn.isEnabled = !value
        qualitySpinner.isEnabled = !value
        urlInput.isEnabled = !value
        progressBar.visibility = if (value) View.VISIBLE else View.INVISIBLE
        progressBar.isIndeterminate = value
        progressBar.progress = 0
    }

    companion object {
        private const val PROCESS_ID = "netgrab_download"
        private const val FOLDER = "NetGrab"
        private const val PREF_LAST_UPDATE = "last_engine_update"
        private val URL_REGEX = Regex("""https?://\S+""")
    }
}
