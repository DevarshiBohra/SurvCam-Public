package org.example.survcam.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R

/**
 * Shows snapshot filenames (e.g. "18-38-05.jpg") as formatted times ("18:38:05").
 *
 * Tap           → open full-screen viewer (when not in selection mode)
 * Long-press    → enter selection mode; that item is selected
 * Tap (sel mode)→ toggle selection on that item
 * All selected cleared → automatically exits selection mode
 */
class SnapshotAdapter(
    private val onItemClick:        (String) -> Unit,
    private val onSelectionChanged: (Int)    -> Unit
) : RecyclerView.Adapter<SnapshotAdapter.ViewHolder>() {

    private val items         = mutableListOf<String>()
    private val selectedItems = mutableSetOf<String>()
    var isSelectionMode       = false
        private set

    fun submit(newItems: List<String>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    fun getSelectedItems(): Set<String> = selectedItems.toSet()

    fun exitSelectionMode() {
        isSelectionMode = false
        selectedItems.clear()
        notifyDataSetChanged()
        onSelectionChanged(0)
    }

    override fun getItemCount() = items.size

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
        ViewHolder(LayoutInflater.from(parent.context).inflate(R.layout.item_snapshot, parent, false))

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val filename = items[position]
        holder.timeText.text = filename.removeSuffix(".jpg").replace("-", ":")

        if (isSelectionMode) {
            holder.checkBox.visibility = View.VISIBLE
            holder.checkBox.isChecked  = filename in selectedItems
        } else {
            holder.checkBox.visibility = View.GONE
        }

        holder.itemView.setOnClickListener {
            if (isSelectionMode) toggleSelection(filename, position)
            else                 onItemClick(filename)
        }

        holder.itemView.setOnLongClickListener {
            if (!isSelectionMode) {
                isSelectionMode = true
                selectedItems.add(filename)
                notifyDataSetChanged()
                onSelectionChanged(selectedItems.size)
            }
            true
        }
    }

    private fun toggleSelection(filename: String, position: Int) {
        if (filename in selectedItems) selectedItems.remove(filename)
        else                           selectedItems.add(filename)

        if (selectedItems.isEmpty()) {
            isSelectionMode = false
            notifyDataSetChanged()
        } else {
            notifyItemChanged(position)
        }
        onSelectionChanged(selectedItems.size)
    }

    class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val timeText: TextView = view.findViewById(R.id.timeText)
        val checkBox: CheckBox = view.findViewById(R.id.checkBox)
    }
}
