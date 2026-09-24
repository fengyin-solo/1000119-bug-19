import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from '@/views/Dashboard.vue'
const Order = () => import('@/views/order/index.vue')
const Waybill = () => import('@/views/waybill/index.vue')
const Vehicle = () => import('@/views/vehicle/index.vue')
const Driver = () => import('@/views/driver/index.vue')
const Temperature = () => import('@/views/temperature/index.vue')
const Excursion = () => import('@/views/excursion/index.vue')
const Warehouse = () => import('@/views/warehouse/index.vue')
const Inbound = () => import('@/views/inbound/index.vue')
const Outbound = () => import('@/views/outbound/index.vue')
const Inventory = () => import('@/views/inventory/index.vue')
const Trace = () => import('@/views/trace/index.vue')
const Quality = () => import('@/views/quality/index.vue')
const Route = () => import('@/views/route/index.vue')
const Dispatch = () => import('@/views/dispatch/index.vue')
const Device = () => import('@/views/device/index.vue')
const Maint = () => import('@/views/maint/index.vue')
const Alarm = () => import('@/views/alarm/index.vue')
const Customer = () => import('@/views/customer/index.vue')
const Billing = () => import('@/views/billing/index.vue')
const Report = () => import('@/views/report/index.vue')
const ReportDetail = () => import('@/views/report/detail.vue')
const Setting = () => import('@/views/setting/index.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
    { path: '/order', name: 'order', component: Order },
    { path: '/waybill', name: 'waybill', component: Waybill },
    { path: '/vehicle', name: 'vehicle', component: Vehicle },
    { path: '/driver', name: 'driver', component: Driver },
    { path: '/temperature', name: 'temperature', component: Temperature },
    { path: '/excursion', name: 'excursion', component: Excursion },
    { path: '/warehouse', name: 'warehouse', component: Warehouse },
    { path: '/inbound', name: 'inbound', component: Inbound },
    { path: '/outbound', name: 'outbound', component: Outbound },
    { path: '/inventory', name: 'inventory', component: Inventory },
    { path: '/trace', name: 'trace', component: Trace },
    { path: '/quality', name: 'quality', component: Quality },
    { path: '/route', name: 'route', component: Route },
    { path: '/dispatch', name: 'dispatch', component: Dispatch },
    { path: '/device', name: 'device', component: Device },
    { path: '/maint', name: 'maint', component: Maint },
    { path: '/alarm', name: 'alarm', component: Alarm },
    { path: '/customer', name: 'customer', component: Customer },
    { path: '/billing', name: 'billing', component: Billing },
    { path: '/report', name: 'report', component: Report },
    { path: '/report/:id', name: 'report-detail', component: ReportDetail },
    { path: '/setting', name: 'setting', component: Setting },
  ],
})

export default router
