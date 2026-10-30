package org.example.survcam.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import java.text.SimpleDateFormat
import java.util.Locale

class DayAdapter(private val onClick: (String) -> Unit) : RecyclerView.Adapter<DayAdapter.DayViewHolder>() {
    private val days = mutableListOf<String>()

    fun submit(items: List<String>) {
        days.clear()
        days.addAll(items)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): DayViewHolder {
        val view = LayoutInflater.from(parent.context).inflate(R.layout.item_day, parent, false)
        return DayViewHolder(view)
    }

    override fun getItemCount(): Int = days.size

    override fun onBindViewHolder(holder: DayViewHolder, position: Int) {
        holder.bind(days[position], onClick)
    }

    class DayViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val dateText: TextView = itemView.findViewById(R.id.dateText)
        private val dayNameText: TextView = itemView.findViewById(R.id.dayNameText)

        fun bind(date: String, onClick: (String) -> Unit) {
            dateText.text = date
            dayNameText.text = formatDayName(date)
            itemView.setOnClickListener { onClick(date) }
        }

        private fun formatDayName(date: String): String {
            return try {
                val input = SimpleDateFormat("yyyy-MM-dd", Locale.getDefault())
                val output = SimpleDateFormat("EEEE", Locale.getDefault())
                output.format(input.parse(date)!!)
            } catch (e: Exception) {
                ""
            }
        }
    }
}
