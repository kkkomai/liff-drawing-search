// Mock fixtures for UI review without a backend.
//
// The mock path still called API.login(), so opening the tab without the
// FastAPI service produced "HTTP 404" instead of the UI being reviewable.
// These fixtures let the screen render (and let the API stubs below return
// empty-but-valid payloads) so the layout, calendar and forms can be checked
// on a device. Nothing here is used in the real auth path.

window.__BENTO_MOCK__ = {
  employee: {
    id: 'emp-001',
    employee_id: 'EMP001',
    name: '山田 太郎',
    department: ' 第一部',
    role: 'employee'
  },

  admin: {
    id: 'emp-000',
    employee_id: 'EMP000',
    name: '管理者 太郎',
    department: ' 管理部',
    role: 'admin'
  },

  // A fortnight of orders so the day list and totals have something to render.
  orders: (function () {
    var out = [];
    var today = new Date();
    for (var i = -13; i <= 14; i++) {
      var d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + i);
      var iso = d.getFullYear() + '-' +
        ('0' + (d.getMonth() + 1)).slice(-2) + '-' +
        ('0' + d.getDate()).slice(-2);
      if (i % 7 === 3 || i === 0) continue; // days off
      out.push({
        id: 'ord-' + i,
        date: iso,
        menu: i % 3 === 0 ? '唐揚げ弁当' : (i % 3 === 1 ? '鮭弁当' : '牛肉丼'),
        count: 1,
        note: '',
        created_at: iso + 'T09:00:00+09:00'
      });
    }
    return out;
  })()
};