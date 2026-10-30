package org.example.survcam.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import org.example.survcam.R
import org.example.survcam.models.Camera
import org.example.survcam.utils.NetworkUtils

class CameraAdapter(private val onClick: (Camera) -> Unit) : RecyclerView.Adapter<CameraAdapter.CameraViewHolder>() {
    private val cameras = mutableListOf<Camera>()

    fun submit(items: List<Camera>) {
        cameras.clear()
        cameras.addAll(items)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): CameraViewHolder {
        val view = LayoutInflater.from(parent.context).inflate(R.layout.item_camera, parent, false)
        return CameraViewHolder(view)
    }

    override fun getItemCount(): Int = cameras.size

    override fun onBindViewHolder(holder: CameraViewHolder, position: Int) {
        holder.bind(cameras[position], onClick)
    }

    class CameraViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val statusDot: View = itemView.findViewById(R.id.statusDot)
        private val nameText: TextView = itemView.findViewById(R.id.nameText)
        private val detailsText: TextView = itemView.findViewById(R.id.detailsText)

        fun bind(camera: Camera, onClick: (Camera) -> Unit) {
            val isOnline = camera.status.equals("online", ignoreCase = true)
            statusDot.setBackgroundResource(if (isOnline) R.drawable.status_online else R.drawable.status_offline)
            nameText.text = camera.name ?: camera.cameraId
            detailsText.text = NetworkUtils.formatCameraDetails(
                camera.cameraId,
                camera.status,
                camera.ipAddress,
                camera.lastSeen
            )
            itemView.setOnClickListener { onClick(camera) }
        }
    }
}
