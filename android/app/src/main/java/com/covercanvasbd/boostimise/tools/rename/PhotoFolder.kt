package com.covercanvasbd.boostimise.tools.rename

import android.net.Uri
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import com.covercanvasbd.boostimise.core.Storage

/** A file or folder inside the folder the user picked (Android's folder picker, so the app may rename there). */
data class Doc(val tree: Uri, val id: String, val name: String, val mime: String, val size: Long) {
    val uri: Uri get() = DocumentsContract.buildDocumentUriUsingTree(tree, id)
    val isDir get() = mime == Document.MIME_TYPE_DIR
}

/** Renaming, moving and copying in the picked folder (all blocking: call them on Dispatchers.IO). */
object PhotoFolder {
    private val resolver get() = Storage.app.contentResolver

    fun root(tree: Uri): Doc {
        val id = DocumentsContract.getTreeDocumentId(tree)
        return find(tree, id) ?: Doc(tree, id, id.substringAfterLast('/').substringAfterLast(':'), Document.MIME_TYPE_DIR, 0)
    }

    /** "Pictures/Covers/24.09.26" for the folder's id "primary:Pictures/Covers/24.09.26". */
    fun label(tree: Uri): String = DocumentsContract.getTreeDocumentId(tree).substringAfter(':').ifEmpty { "Phone storage" }

    private val COLUMNS = arrayOf(Document.COLUMN_DOCUMENT_ID, Document.COLUMN_DISPLAY_NAME, Document.COLUMN_MIME_TYPE,
        Document.COLUMN_SIZE)

    private fun find(tree: Uri, id: String): Doc? =
        resolver.query(DocumentsContract.buildDocumentUriUsingTree(tree, id), COLUMNS, null, null, null)?.use { c ->
            if (c.moveToFirst()) Doc(tree, c.getString(0), c.getString(1) ?: "", c.getString(2) ?: "", c.getLong(3)) else null
        }

    fun children(dir: Doc): List<Doc> {
        val out = mutableListOf<Doc>()
        resolver.query(DocumentsContract.buildChildDocumentsUriUsingTree(dir.tree, dir.id), COLUMNS, null, null, null)?.use { c ->
            while (c.moveToNext()) out += Doc(dir.tree, c.getString(0), c.getString(1) ?: "", c.getString(2) ?: "", c.getLong(3))
        }
        return out
    }

    fun read(doc: Doc): ByteArray = resolver.openInputStream(doc.uri)!!.use { it.readBytes() }

    /** Renames a file; returns it under its new name (its id changes with the name). */
    fun rename(doc: Doc, name: String): Doc {
        val uri = DocumentsContract.renameDocument(resolver, doc.uri, name) ?: throw IllegalStateException("rename refused")
        return doc.copy(id = DocumentsContract.getDocumentId(uri), name = name)
    }

    fun mkdir(parent: Doc, name: String): Doc {
        val uri = DocumentsContract.createDocument(resolver, parent.uri, Document.MIME_TYPE_DIR, name)
            ?: throw IllegalStateException("could not make the folder $name")
        return Doc(parent.tree, DocumentsContract.getDocumentId(uri), name, Document.MIME_TYPE_DIR, 0)
    }

    fun move(doc: Doc, from: Doc, to: Doc): Doc {
        val uri = DocumentsContract.moveDocument(resolver, doc.uri, from.uri, to.uri) ?: throw IllegalStateException("move refused")
        return doc.copy(id = DocumentsContract.getDocumentId(uri))
    }

    /** A copy of [doc] named [name] in [dir]. */
    fun copy(doc: Doc, dir: Doc, name: String): Doc {
        val uri = DocumentsContract.createDocument(resolver, dir.uri, doc.mime.ifEmpty { "image/jpeg" }, name)
            ?: throw IllegalStateException("could not create $name")
        try {
            resolver.openInputStream(doc.uri)!!.use { input -> resolver.openOutputStream(uri)!!.use { input.copyTo(it) } }
        } catch (e: Exception) {
            runCatching { DocumentsContract.deleteDocument(resolver, uri) }
            throw e
        }
        val id = DocumentsContract.getDocumentId(uri)
        return find(doc.tree, id) ?: doc.copy(id = id, name = name)
    }

    /** Removes a file this tool made (Undo of a copy), or a folder it made if it is empty. */
    fun deleteOwn(tree: Uri, id: String, dir: Boolean): Boolean {
        val doc = find(tree, id) ?: return false
        if (dir && children(doc).isNotEmpty()) return false
        return DocumentsContract.deleteDocument(resolver, doc.uri)
    }

    fun byId(tree: Uri, id: String): Doc? = runCatching { find(tree, id) }.getOrNull()
}
