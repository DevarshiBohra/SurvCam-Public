package org.example.survcam.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import org.example.survcam.models.HourSlot

class HourAdapter(private val onClick: (Int) -> Unit) : RecyclerView.Adapter<HourAdapter.HourViewHolder>() {
    private val hours = mutableListOf<HourSlot>()

    fun submit(items: List<HourSlot>) {
        hours.clear()
        hours.addAll(items)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): HourViewHolder {
        val view = LayoutInflater.from(parent.context).inflate(R.layout.item_hour, parent, false)
        return HourViewHolder(view)
    }

    override fun getItemCount(): Int = hours.size

    override fun onBindViewHolder(holder: HourViewHolder, position: Int) {
        holder.bind(hours[position], onClick)
    }

    class HourViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val hourText: TextView = itemView.findViewById(R.id.hourText)

        fun bind(slot: HourSlot, onClick: (Int) -> Unit) {
            hourText.text = formatHourRange(slot.hour)
            itemView.alpha = if (slot.hasRecording) 1.0f else 0.35f
            itemView.isEnabled = slot.hasRecording
            itemView.setOnClickListener {
                if (slot.hasRecording) onClick(slot.hour)
            }
        }

        private fun formatHourRange(hour: Int): String {
            val start = formatHour(hour)
            val end = formatHour((hour + 1) % 24)
            return "$start - $end"
        }

        private fun formatHour(hour: Int): String {
            val period = if (hour < 12) "AM" else "PM"
            val displayHour = when {
                hour == 0 -> 12
                hour > 12 -> hour - 12
                else -> hour
            }
            return "$displayHour $period"
        }
    }
}
