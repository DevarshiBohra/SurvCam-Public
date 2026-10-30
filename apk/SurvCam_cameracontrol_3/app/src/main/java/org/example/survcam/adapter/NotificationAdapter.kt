package org.example.survcam.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import org.example.survcam.models.NotificationItem
import java.text.SimpleDateFormat
import java.util.Locale

class NotificationAdapter : RecyclerView.Adapter<NotificationAdapter.NotificationViewHolder>() {
    private val items = mutableListOf<NotificationItem>()

    fun submit(newItems: List<NotificationItem>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): NotificationViewHolder {
        val view = LayoutInflater.from(parent.context).inflate(R.layout.item_notification, parent, false)
        return NotificationViewHolder(view)
    }

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: NotificationViewHolder, position: Int) {
        holder.bind(items[position])
    }

    class NotificationViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val titleText: TextView = itemView.findViewById(R.id.notifTitle)
        private val bodyText: TextView = itemView.findViewById(R.id.notifBody)
        private val timeText: TextView = itemView.findViewById(R.id.notifTime)

        fun bind(item: NotificationItem) {
            titleText.text = item.title
            bodyText.text = item.body
            timeText.text = formatTime(item.createdAt)
        }

        private fun formatTime(iso: String): String {
            return try {
                val input = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.getDefault())
                val output = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())
                val date = input.parse(iso.substringBefore("."))
                if (date != null) output.format(date) else iso
            } catch (e: Exception) {
                iso
            }
        }
    }
}
