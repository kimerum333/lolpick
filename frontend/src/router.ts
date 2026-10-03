import { createRouter, createWebHashHistory } from 'vue-router'
import MatchesView from './views/MatchesView.vue'
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: MatchesView, meta: { title: '경기' } },
    { path: '/ranking', component: () => import('./views/RankingView.vue'), meta: { title: '랭킹' } },
    { path: '/profile', component: () => import('./views/ProfileView.vue'), meta: { title: '내 프로필' } },
    { path: '/guide', component: () => import('./views/GuideView.vue'), meta: { title: '이용 가이드' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
router.afterEach(to => { document.title = `${String(to.meta.title)} · LOLPICK` })
export default router
