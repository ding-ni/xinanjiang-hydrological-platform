<template><ElDialog v-model="open" append-to-body destroy-on-close :show-close="false" width="min(1000px, 94vw)" class="hydrology-dialog"><slot /></ElDialog></template>
<script setup>
import { ref } from 'vue'
import { ElDialog } from 'element-plus'
const open=ref(false)
defineExpose({showModal:()=>{open.value=true},close:()=>{open.value=false}})
</script>
<style>.hydrology-dialog{max-height:90vh;overflow:auto;padding:24px}.hydrology-dialog .el-dialog__header{display:none}.hydrology-dialog .dialog-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}</style>
