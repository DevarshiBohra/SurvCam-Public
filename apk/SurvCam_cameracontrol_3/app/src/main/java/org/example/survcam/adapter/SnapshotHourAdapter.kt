package org.example.survcam.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R

class SnapshotHourAdapter(
    private val onClick: (Int) -> Unit
) : RecyclerView.Adapter<SnapshotHourAdapter.ViewHolder>() {

    private val items = mutableListOf<Int>()

    fun submit(hours: List<Int>) {
        items.clear()
        items.addAll(hours)
        notifyDataSetChanged()
    }

    override fun getItemCount() = items.size

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
        ViewHolder(LayoutInflater.from(parent.context).inflate(R.layout.item_snapshot_hour, parent, false))

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val hour = items[position]
        holder.hourText.text  = formatHour(hour)
        holder.countText.text = "Tap to view snapshots"
        holder.itemView.setOnClickListener { onClick(hour) }
    }

    private fun formatHour(h: Int): String {
        fun label(n: Int) = when {
            n == 0  -> "12 AM"
            n < 12  -> "$n AM"
            n == 12 -> "12 PM"
            else    -> "${n - 12} PM"
        }
        return "${label(h)}  –  ${label((h + 1) % 24)}"
    }

    class ViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val hourText:  TextView = view.findViewById(R.id.hourText)
        val countText: TextView = view.findViewById(R.id.countText)
    }
}
